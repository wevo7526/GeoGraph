"""The market-as-sensor loop — build-spec section 4, the forward-looking core.

The true mechanisms are private information we do not have. So: estimate the
latent variables (position, salience, clout, resolve) from observable
proxies, hold them as DISTRIBUTIONS, and treat the market reaction as a
second sensor. The residual between the expected and the REALIZED market
move — both computed by the deterministic transmission engine — is our
estimate of the private information open sources could not show, and it
updates the AttributeEstimate distributions.

THE LOOP IS POWERED ONLY BY REALIZED OUTCOMES, NEVER BY THE MODEL'S OWN
PREDICTIONS. Structurally enforced: the only inputs read here are AFFECTED
edges the transmission engine measured from the panel — there is no code
path from a Forecast into this module, and no future refactor should add
one.

Updates write NEW AttributeEstimate nodes (method='sensor_update') — the old
estimate is history, not overwritten state, so the trajectory of belief is
itself queryable.

The update rule, stated so it can be argued with: the constant-mean model
already prices the EXPECTED move (the estimation-window mean), so the
abnormal return IS the surprise. A significant surprise on a material-
conflict event revises the RESOLVE estimate of both parties upward — the
market judged the confrontation more serious than open sources implied — by
a step proportional to the capped t-statistic; the standard deviation
tightens with each realized observation. An insignificant effect updates
NOTHING: no signal, no belief change.
"""

from __future__ import annotations

import math
from typing import Any

from core.graph import kuzu_store

#: Update step per unit of capped |t|; the cap keeps one violent print from
#: rewriting an actor's whole history.
_STEP = 0.1
_T_CAP = 3.0
#: Significance gate: effects the study could not distinguish from noise
#: carry no information for the loop.
_P_GATE = 0.1
#: Each realized observation tightens the belief; the floor keeps it a
#: distribution — certainty is not on offer here.
_STD_DECAY = 0.9
_STD_FLOOR = 0.2
_PRIOR_MEAN = 0.0
_PRIOR_STD = 1.0


def _strongest_effect(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The single most informative measured effect: finite t, lowest p, ties
    broken by finest window then ticker — deterministic, no averaging of
    windows that measure the same move twice."""
    finite = [
        r for r in rows
        if r["p_value"] is not None and math.isfinite(float(r["p_value"]))
    ]
    if not finite:
        return None
    return min(finite, key=lambda r: (float(r["p_value"]), r["window"], r["ticker"]))


def update_from_effect(conn: Any, event_node_id: str) -> list[dict[str, Any]]:
    """Read the event's MEASURED AFFECTED edges, compare against the expected
    move given current estimates, and write updated resolve AttributeEstimate
    rows for the actors involved. Returns what it wrote.

    Refuses an unmeasured event outright: with no realized outcome there is
    nothing the loop is allowed to learn from.
    """
    effects = kuzu_store.query(
        conn,
        "MATCH (e:Event {node_id: $id})-[a:AFFECTED]->(m:Market) "
        "RETURN m.ticker AS ticker, a.window AS window, a.abnormal_return AS abnormal, "
        "a.t_stat AS t_stat, a.p_value AS p_value",
        {"id": event_node_id},
    )
    if not effects:
        raise ValueError(
            f"{event_node_id} has no measured effects — the sensor loop updates "
            "from REALIZED outcomes only (build-spec section 4), and none exist."
        )

    event = kuzu_store.query(
        conn,
        "MATCH (e:Event {node_id: $id}) RETURN e.event_time AS event_time, "
        "e.quad_class AS quad_class",
        {"id": event_node_id},
    )[0]

    strongest = _strongest_effect(effects)
    if (
        strongest is None
        or float(strongest["p_value"]) >= _P_GATE
        or event["quad_class"] != "material_conflict"
    ):
        return []  # no signal, no belief change — silence is the honest update

    step = _STEP * min(_T_CAP, abs(float(strongest["t_stat"])))

    actors = kuzu_store.query(
        conn,
        "MATCH (e:Event {node_id: $id}) "
        "OPTIONAL MATCH (e)-[:INITIATED_BY]->(i:Actor) "
        "OPTIONAL MATCH (e)-[:DIRECTED_AT]->(t:Actor) "
        "RETURN i.node_id AS initiator, t.node_id AS target",
        {"id": event_node_id},
    )[0]
    written: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    for actor_id in dict.fromkeys(
        a for a in (actors["initiator"], actors["target"]) if a
    ):
        prior_rows = kuzu_store.query(
            conn,
            "MATCH (a:Actor {node_id: $id})-[:HAS_ESTIMATE]->(s:AttributeEstimate) "
            "WHERE s.attribute = 'resolve' "
            "RETURN s.value_mean AS mean, s.value_std AS std, s.as_of AS as_of "
            "ORDER BY s.as_of DESC LIMIT 1",
            {"id": actor_id},
        )
        prior_mean = float(prior_rows[0]["mean"]) if prior_rows else _PRIOR_MEAN
        prior_std = float(prior_rows[0]["std"]) if prior_rows else _PRIOR_STD

        estimate_id = (
            f"estimate:resolve:{actor_id.split(':', 1)[1]}:"
            f"{event_node_id.split(':', 1)[1]}"
        )
        row = {
            "node_id": estimate_id,
            "attribute": "resolve",
            "value_mean": round(prior_mean + step, 6),
            "value_std": round(max(_STD_FLOOR, prior_std * _STD_DECAY), 6),
            "as_of": str(event["event_time"]),
            "method": "sensor_update",
        }
        written.append(row)
        edges.append({"src": actor_id, "dst": estimate_id})

    if written:
        kuzu_store.merge_nodes(conn, "AttributeEstimate", written)
        kuzu_store.merge_edges(conn, "HAS_ESTIMATE", edges)
    return written
