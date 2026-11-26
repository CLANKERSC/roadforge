"""Helpers shared by the build stages: hashing, stage markers, selection loading."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from forge_worker.selection import Selection, SelectionRequest, build_selection


def hash_of(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


def write_json(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(obj, indent=2, sort_keys=True), encoding="utf-8")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def stage_is_fresh(workdir: Path, stage: str, inputs_hash: str, outputs: list[str]) -> bool:
    """True if this stage already ran with the same inputs and its files are still there."""
    marker = workdir / f"{stage}.stage.json"
    if not marker.is_file():
        return False
    data = read_json(marker)
    return data.get("inputs") == inputs_hash and all((workdir / o).is_file() for o in outputs)


def mark_stage(workdir: Path, stage: str, inputs_hash: str, outputs: list[str]) -> None:
    write_json(workdir / f"{stage}.stage.json", {"stage": stage, "inputs": inputs_hash, "outputs": outputs})


def stage_inputs(workdir: Path, stage: str) -> str:
    """The inputs hash a finished earlier stage ran with (chains the stages together)."""
    return str(read_json(workdir / f"{stage}.stage.json")["inputs"])


def selection_from_params(params: dict[str, Any]) -> Selection:
    return build_selection(SelectionRequest.model_validate(params["selection"]))
