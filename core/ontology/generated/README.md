# Generated ontology artifacts

BUILD OUTPUTS — do not edit, do not commit (gitignored except this file).

`python scripts/generate_ontology.py` (requires `pip install -e ".[gen]"`)
writes here:

- `models.py` — Pydantic v2 models via `gen-pydantic`, used at the INGESTION
  boundary so a bad record fails where it is born.
- `geograph.schema.json` — JSON Schema via `gen-json-schema`, the validate-
  every-record contract from build-spec section 8.1.

The runtime storage boundary does NOT read these: `core/ontology/kuzu_schema.py`
derives the Kuzu DDL and validators straight from the LinkML YAML at process
start. One source of truth, two generated views.
