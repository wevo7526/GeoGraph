"""Model artifacts on disk: JSON, versioned, hashed.

JSON rather than pickle for three reasons that all matter here. It is
readable, so a weight vector can be inspected in a diff instead of taken on
faith. It is safe to load, where pickle executes whatever it is handed. And
it needs nothing at inference beyond numpy — the container already carries
that, and a model that dragged a training framework into the API would make
the boot heavier for no gain.

The artifact carries what a prediction needs to be defensible later: the
feature order it was fitted with, the span it was trained on, the
walk-forward scores it passed the gate with, and a hash of the whole payload.
A Forecast frozen from this model records that hash, so a call can be traced
back to the exact weights that made it.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from core.models import features as feature_module

#: Committed to the repo, not written to the volume: the artifact belongs to
#: the image so a deploy is reproducible, and it is small enough (a handful
#: of float vectors) that versioning it in git is the simple answer.
MODELS_DIR = Path(__file__).resolve().parent.parent.parent / "models"

ARTIFACT_VERSION = 1


class ArtifactError(RuntimeError):
    """The artifact is absent or does not match the code that would use it."""


def _digest(payload: dict[str, Any]) -> str:
    """Stable hash of everything except the hash field itself."""
    body = {k: v for k, v in payload.items() if k != "hash"}
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:16]


def build_artifact(
    *,
    name: str,
    weights: dict[int, list[float]],
    scaler_mean: list[float],
    scaler_sd: list[float],
    target: str,
    folds: list[dict[str, Any]],
    gate: tuple[bool, str],
    train_span: tuple[str, str],
    residual_sd: dict[int, float],
    rows: int,
    dyads: int,
) -> dict[str, Any]:
    passed, reason = gate
    payload: dict[str, Any] = {
        "artifact_version": ARTIFACT_VERSION,
        "name": name,
        # The feature ORDER is part of the contract: weights are positional,
        # so an artifact fitted against a different order would score every
        # prediction silently wrong rather than fail.
        "features": list(feature_module.SHIPPED_FEATURES),
        "target": target,
        # The scaler ships with the weights — standardised columns are part
        # of the model, not a preprocessing detail.
        "scaler_mean": [round(v, 8) for v in scaler_mean],
        "scaler_sd": [round(v, 8) for v in scaler_sd],
        "horizons": sorted(weights),
        "weights": {str(h): w for h, w in sorted(weights.items())},
        "residual_sd": {str(h): round(v, 6) for h, v in sorted(residual_sd.items())},
        "train_span": list(train_span),
        "rows": rows,
        "dyads": dyads,
        "gate_passed": passed,
        "gate_reason": reason,
        "walk_forward": folds,
    }
    payload["hash"] = _digest(payload)
    return payload


def save(artifact: dict[str, Any], path: Path | None = None) -> Path:
    target = path or MODELS_DIR / f"{artifact['name']}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "w", encoding="utf-8") as fh:
        json.dump(artifact, fh, indent=2, sort_keys=False)
        fh.write("\n")
    return target


def load(name: str, path: Path | None = None) -> dict[str, Any]:
    """Load and CHECK. An artifact whose feature order no longer matches the
    code is refused loudly here rather than serving quietly wrong numbers."""
    target = path or MODELS_DIR / f"{name}.json"
    if not target.exists():
        raise ArtifactError(
            f"no model artifact at {target} — run scripts/train_forecaster.py"
        )
    with open(target, encoding="utf-8") as fh:
        artifact: dict[str, Any] = json.load(fh)
    if artifact.get("features") != list(feature_module.SHIPPED_FEATURES):
        raise ArtifactError(
            f"{target} was fitted with features {artifact.get('features')}, but the "
            f"code now ships {list(feature_module.SHIPPED_FEATURES)}. Weights are "
            "positional — retrain rather than reorder."
        )
    if artifact.get("hash") != _digest(artifact):
        raise ArtifactError(f"{target} has been edited by hand — its hash does not match.")
    return artifact


def weights_of(artifact: dict[str, Any]) -> dict[int, list[float]]:
    return {int(h): w for h, w in artifact["weights"].items()}


def residual_sd_of(artifact: dict[str, Any]) -> dict[int, float]:
    return {int(h): float(v) for h, v in artifact.get("residual_sd", {}).items()}


def scaler_of(artifact: dict[str, Any]) -> tuple[list[float], list[float]]:
    return (
        [float(v) for v in artifact["scaler_mean"]],
        [float(v) for v in artifact["scaler_sd"]],
    )


def available(path: Path | None = None) -> list[str]:
    directory = path or MODELS_DIR
    if not directory.exists():
        return []
    return sorted(p.stem for p in directory.glob("*.json"))
