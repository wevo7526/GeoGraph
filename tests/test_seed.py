"""The seed, end to end on a real graph, and the boot sequence around it.

The seed is the only thing that writes the spine, so these tests are where
"the graph the reader sees" is pinned: idempotent, provenance-clean, coded by
Head B, and honest about edges it failed to write.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

from core import packs
from core.graph import kuzu_store

_ROOT = Path(__file__).resolve().parent.parent


def _load(name: str):
    """Import a file under scripts/ — not a package, so not importable."""
    spec = importlib.util.spec_from_file_location(name, _ROOT / "scripts" / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


seed_pack = _load("seed_pack")
boot = _load("boot")


@pytest.fixture()
def conn(tmp_path):
    # Closed, not dereferenced — see the note in tests/test_store.py.
    connection = kuzu_store.connect(tmp_path / "seed.kuzu")
    yield connection
    kuzu_store.close(connection)


@pytest.fixture()
def mena():
    return packs.load("mena")


def test_seed_writes_the_spine_and_cites_itself(conn, mena):
    counts = seed_pack.seed(conn, mena)
    assert counts["events"] == len(mena.marquee_events)
    assert counts["actors"] == len(mena.actors)
    assert counts["OF_DYAD"] == counts["events"]
    assert kuzu_store.check_provenance(conn) == []


def test_seeding_twice_is_the_same_graph(conn, mena):
    first = seed_pack.seed(conn, mena)
    second = seed_pack.seed(conn, mena)
    assert first == second
    events = kuzu_store.query(conn, "MATCH (e:Event) RETURN count(*) AS n")[0]["n"]
    assert events == len(mena.marquee_events)


def test_every_seeded_event_is_scored_and_classified(conn, mena):
    seed_pack.seed(conn, mena)
    rows = kuzu_store.query(
        conn,
        "MATCH (e:Event) RETURN e.node_id AS id, e.goldstein AS g, "
        "e.escalation_direction AS dir, e.escalation_baseline AS base",
    )
    assert len(rows) == len(mena.marquee_events)
    for row in rows:
        assert row["g"] is not None, f"{row['id']} reached the graph unscored"
        assert row["dir"] in {"escalating", "stable", "deescalating"}
        assert row["base"] is not None


def test_the_proxy_web_is_seeded_and_sourced(conn, mena):
    # The durable network from actors.yaml `relations:` — Iran's clients are
    # RELATES_TO edges with a citation, not narrative color.
    counts = seed_pack.seed(conn, mena)
    assert counts["RELATES_TO"] == len(mena.relations) > 0
    rows = kuzu_store.query(
        conn,
        "MATCH (a:Actor)-[r:RELATES_TO]->(b:Actor) "
        "RETURN a.node_id AS a, b.node_id AS b, r.relation_type AS t, "
        "r.source_id AS s, r.valid_from AS vf",
    )
    proxies = {(row["a"], row["b"]) for row in rows if row["t"] == "proxy"}
    assert ("actor:cow-630", "actor:hezbollah") in proxies
    assert ("actor:cow-630", "actor:hamas") in proxies
    assert ("actor:cow-630", "actor:ansar-allah") in proxies
    for row in rows:
        assert row["s"], f"{row['a']} → {row['b']} is unsourced"
        assert row["vf"], f"{row['a']} → {row['b']} has no validity start"


def test_a_relation_naming_a_ghost_actor_is_refused_at_load(mena):
    broken_actors = {
        **mena.data["actors"],
        "relations": [{"a": "actor:cow-630", "b": "actor:nobody",
                       "relation_type": "proxy", "valid_from": "2000",
                       "source": "source:crs-iran-proxies"}],
    }
    with pytest.raises(packs.PackError, match="not an actor"):
        packs._validate(packs.Pack(
            name=mena.name, path=mena.path, data={**mena.data, "actors": broken_actors},
        ))


def test_a_relation_citing_an_undeclared_source_is_refused_at_load(mena):
    broken_actors = {
        **mena.data["actors"],
        "relations": [{"a": "actor:cow-630", "b": "actor:hamas",
                       "relation_type": "proxy", "valid_from": "1992",
                       "source": "source:vibes"}],
    }
    with pytest.raises(packs.PackError, match="not in sources.yaml"):
        packs._validate(packs.Pack(
            name=mena.name, path=mena.path, data={**mena.data, "actors": broken_actors},
        ))


def test_dyads_exist_for_every_of_dyad_edge(conn, mena):
    # The ordering guarantee: dyads are written before the edges citing them,
    # so no OF_DYAD edge can dangle.
    seed_pack.seed(conn, mena)
    edges = kuzu_store.query(conn, "MATCH ()-[r:OF_DYAD]->() RETURN count(*) AS n")[0]["n"]
    assert edges == len(mena.marquee_events)


def test_an_uncoded_event_is_refused(conn, mena):
    broken = packs.Pack(
        name=mena.name,
        path=mena.path,
        data={**mena.data, "marquee_events": {"events": [
            {"id": "event:x", "date": "2020-01-01", "name": "Uncoded", "cameo": None},
        ]}},
    )
    with pytest.raises(seed_pack.SeedError, match="no CAMEO code"):
        seed_pack.seed(conn, broken)


def test_an_event_citing_a_ghost_actor_fails_the_seed(conn, mena):
    # A dangling edge writes nothing while still counting as attempted — the
    # exact silent gap `_written` exists to catch.
    broken = packs.Pack(
        name=mena.name,
        path=mena.path,
        data={**mena.data, "marquee_events": {"events": [
            {"id": "event:ghost", "date": "2020-01-01", "name": "Ghost target",
             "cameo": "190", "quad_class": "material_conflict",
             "initiator": "actor:cow-2", "target": "actor:nobody"},
        ]}},
    )
    with pytest.raises(seed_pack.SeedError, match="in the graph"):
        seed_pack.seed(conn, broken)


# ── the boot sequence ────────────────────────────────────────────────────────


def test_boot_seeds_every_complete_pack_by_default(monkeypatch):
    # The rule, not a roster: boot seeds whatever satisfies the contract, so a
    # new pack is picked up by existing, not by editing this list.
    monkeypatch.delenv("GEOGRAPH_SEED_PACKS", raising=False)
    assert boot._pack_names() == packs.available()
    assert {"china", "eurasia", "mena"} <= set(packs.available())


def test_boot_honours_an_explicit_pack_list(monkeypatch):
    monkeypatch.setenv("GEOGRAPH_SEED_PACKS", "mena, china")
    assert boot._pack_names() == ["mena", "china"]


def test_seeding_can_be_switched_off_for_a_batch_job(monkeypatch):
    monkeypatch.setenv("GEOGRAPH_SEED_ON_BOOT", "0")
    status = boot._boot_status()
    assert status["seeded"] is False
    assert "GEOGRAPH_SEED_ON_BOOT" in status["reason"]


def test_a_failing_seed_reports_rather_than_stopping_the_boot(tmp_path, monkeypatch):
    # THE RULE: a bad pack must not become a restart loop. The status carries
    # the failure and the API still comes up.
    # KUZU_DB_PATH is pinned to tmp_path: without it the child seed opens the
    # repo's own data/ graph, and a dev API holding that lock turns this test
    # into a fight over a file it was never about.
    monkeypatch.setenv("KUZU_DB_PATH", str(tmp_path / "atlantis.kuzu"))
    monkeypatch.setenv("GEOGRAPH_SEED_PACKS", "atlantis")
    monkeypatch.setenv("GEOGRAPH_DEEP_TIER", "0")
    monkeypatch.setenv("GEOGRAPH_13F_ON_BOOT", "0")  # no SEC calls from a test
    monkeypatch.setenv("GEOGRAPH_GDELT_ON_BOOT", "0")
    monkeypatch.delenv("GEOGRAPH_SEED_ON_BOOT", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    status = boot._boot_status()
    assert status["seeded"] is True
    assert status["packs"][0]["ok"] is False
    assert "atlantis" in status["packs"][0]["error"]


def test_the_seed_runs_in_a_child_process_so_the_lock_is_released(tmp_path, monkeypatch):
    # End to end through the real subprocess boundary: seed, then prove the
    # graph can be opened for writing afterwards — which is exactly what the
    # API does next.
    db = tmp_path / "boot.kuzu"
    monkeypatch.setenv("KUZU_DB_PATH", str(db))
    monkeypatch.setenv("GEOGRAPH_SEED_PACKS", "mena")
    monkeypatch.setenv("GEOGRAPH_DEEP_TIER", "0")
    monkeypatch.setenv("GEOGRAPH_13F_ON_BOOT", "0")  # no SEC calls from a test
    monkeypatch.setenv("GEOGRAPH_GDELT_ON_BOOT", "0")
    monkeypatch.delenv("GEOGRAPH_SEED_ON_BOOT", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    status = boot._boot_status()
    assert status["packs"] == [{"pack": "mena", "ok": True, "seconds": pytest.approx(
        status["packs"][0]["seconds"])}]
    writable = kuzu_store.connect(db)
    try:
        assert kuzu_store.query(writable, "MATCH (e:Event) RETURN count(*) AS n")[0]["n"] > 0
    finally:
        kuzu_store.close(writable)


def test_boot_hands_the_status_to_the_app_through_the_environment(tmp_path, monkeypatch):
    # The LEGACY serialised order (GEOGRAPH_API_FIRST=0): boot.py runs every
    # step, then execs the app — so the status has to survive an exec, and an
    # env var does where a Python object does not.
    monkeypatch.setenv("KUZU_DB_PATH", str(tmp_path / "env.kuzu"))
    monkeypatch.setenv("GEOGRAPH_SEED_ON_BOOT", "0")
    monkeypatch.setenv("GEOGRAPH_API_FIRST", "0")
    # boot.py's own contract: it execs the command it is given, exactly as the
    # container invokes it. The exec'd command is a FILE rather than `-c ...`
    # because os.execvp re-quotes arguments on Windows and mangles an inline
    # program; the container is Linux, where argv passes through as an array.
    echo = tmp_path / "echo_status.py"
    echo.write_text("import os\nprint(os.environ['GEOGRAPH_BOOT_STATUS'])\n", encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(_ROOT / "scripts" / "boot.py"), sys.executable, str(echo)],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    assert payload["seeded"] is False


def test_api_first_boot_execs_the_app_immediately_with_the_handoff_flag(
    tmp_path, monkeypatch
):
    # The DEFAULT order since 2026-08-14: bind first, boot behind the port.
    # boot.py must exec the app BEFORE running any step, carrying the flag
    # that tells the app's lifespan to run the boot on a background thread.
    monkeypatch.setenv("KUZU_DB_PATH", str(tmp_path / "env.kuzu"))
    monkeypatch.delenv("GEOGRAPH_API_FIRST", raising=False)
    echo = tmp_path / "echo_flag.py"
    echo.write_text(
        "import os\n"
        "print('flag=' + os.environ.get('GEOGRAPH_RUN_BOOT_IN_APP', ''))\n"
        "print('status=' + os.environ.get('GEOGRAPH_BOOT_STATUS', 'unset'))\n",
        encoding="utf-8",
    )
    proc = subprocess.run(
        [sys.executable, str(_ROOT / "scripts" / "boot.py"), sys.executable, str(echo)],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    lines = proc.stdout.strip().splitlines()
    assert "flag=1" in lines, "the app must be told to run the boot itself"
    assert "status=unset" in lines, "no step may have run before the exec"


def test_health_reads_the_boot_status(monkeypatch):
    from core.api import app as app_module

    monkeypatch.setenv("GEOGRAPH_BOOT_STATUS", json.dumps({"seeded": True, "packs": []}))
    assert app_module._boot_status() == {"seeded": True, "packs": []}
    monkeypatch.setenv("GEOGRAPH_BOOT_STATUS", "not json")
    assert "error" in (app_module._boot_status() or {})
    monkeypatch.delenv("GEOGRAPH_BOOT_STATUS")
    assert app_module._boot_status() is None


def test_the_boot_fingerprint_covers_the_code_that_reads_the_inputs():
    """A loader bug being FIXED must re-derive what the buggy version wrote.

    Production carried COW alliances with no end date: the graph believed
    Britain and Russia were allies on the strength of a 1915 treaty, and the
    United States and Iran on a 1958 one, each sitting beside the rivalry that
    actually characterises the pair. COW had the terminations all along
    (1917-11-08 and 1979-03-12) and `cow.load_alliances` parses them correctly
    — the edges had simply been written by an older version, and the
    fingerprint matched on every boot forever, so `deep` skipped in
    milliseconds and the wrong data stayed.

    Inputs alone are not the input. The code that turns a raw file into graph
    rows is too.
    """
    import scripts.boot as boot

    source = Path(boot.__file__).read_text(encoding="utf-8")
    fingerprint = source[source.index("def _image_fingerprint"):]
    fingerprint = fingerprint[:fingerprint.index("\n\n\n")]
    assert '"ingestion"' in fingerprint, (
        "_image_fingerprint must hash core/ingestion, or a fixed loader never "
        "re-derives what the broken one wrote"
    )
    assert '"ontology"' in fingerprint, "same for the schema the rows are shaped by"
    # And NOT all of core/, which would invalidate every guard on every push —
    # the cost the guards exist to avoid.
    assert '_ROOT / "core"\n' not in fingerprint
