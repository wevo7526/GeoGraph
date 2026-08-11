# China / Taiwan Strait pack — Phase 6

The flagship second region (build-spec decision 8). This directory
deliberately does NOT yet satisfy the pack contract, so `core.packs.available()`
does not list it — absent, not broken.

Phase 6's definition of done: the seven contract files land here
(`actors.yaml`, `issues.yaml`, `markets.yaml`, `assets.yaml`, `priors.yaml`,
`sources.yaml`, `marquee_events.yaml`), **the core runs against them
unchanged**, and the China case study is built. If landing this pack requires
touching anything under `core/`, the contract has failed and that is the bug.
