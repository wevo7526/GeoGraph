"""Generate Pydantic models and JSON Schema from the LinkML ontology.

Outputs land in core/ontology/generated/ (gitignored — they are build
artifacts of the YAML, and two committed copies of one model drift). The
runtime Kuzu DDL does NOT come from here: core/ontology/kuzu_schema.py reads
the YAML directly at process start.

Requires the full LinkML toolchain:  pip install -e ".[gen]"
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = ROOT / "core" / "ontology" / "geograph.linkml.yaml"
OUT = ROOT / "core" / "ontology" / "generated"


def main() -> None:
    for tool in ("gen-pydantic", "gen-json-schema"):
        try:
            subprocess.run([tool, "--help"], capture_output=True, check=True)
        except (FileNotFoundError, subprocess.CalledProcessError):
            sys.exit(f'{tool} is not available — pip install -e ".[gen]"')

    OUT.mkdir(parents=True, exist_ok=True)

    models = subprocess.run(
        ["gen-pydantic", "--pydantic-version", "2", str(SCHEMA)],
        capture_output=True, text=True, check=True,
    )
    (OUT / "models.py").write_text(models.stdout, encoding="utf-8")
    print(f"wrote {OUT / 'models.py'}")

    schema = subprocess.run(
        ["gen-json-schema", str(SCHEMA)],
        capture_output=True, text=True, check=True,
    )
    (OUT / "geograph.schema.json").write_text(schema.stdout, encoding="utf-8")
    print(f"wrote {OUT / 'geograph.schema.json'}")


if __name__ == "__main__":
    main()
