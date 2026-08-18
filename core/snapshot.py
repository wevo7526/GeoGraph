"""The snapshot is the engine's frozen weights.

Cited split (2026-08-17): the scored corpus, the counted kernel, the fitted
payoffs, the hostility model, CINC, RELATES_TO and the measured transmission
map are a TRAINING SET. Live GDELT 2.0 is an overlay scored against those
weights and NEVER written as graph edges.

When frozen (the default), harvest does not append GDELT 1.0 days onto the
corpus and the study does not grow `event_study_runs` from new wire. The
overlay is applied at READ time and re-solves nothing: a region solve is
minutes and gigabytes inside the serving process, so treating a 15-minute
file as a reason to re-solve OOM-killed the container on 2026-08-18. Set
`GEOGRAPH_SNAPSHOT_FROZEN=0` to grow the training set again.
"""

from __future__ import annotations

import os


def frozen() -> bool:
    """Is the snapshot locked as the engine's weights?"""
    return os.getenv("GEOGRAPH_SNAPSHOT_FROZEN", "1") not in ("0", "false", "False")
