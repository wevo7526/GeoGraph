"""The learned layer — docs/ml-spec.md.

Deterministic computation, not the LLM: these modules fit and evaluate a
model over the archive's own counts, and they are held to the same standard
as everything else in `core/` — no clock, no network, no hidden state, and
every number recomputable from the graph.

The invariant they respect (build-spec section 17): a learned prediction is a
NEW Forecast carrying `method='model:<name>@<version>'` and its artifact
hash. It never overwrites a counted forecast, never writes AFFECTED, and
never ships without the walk-forward score that says how much to believe it.
"""
