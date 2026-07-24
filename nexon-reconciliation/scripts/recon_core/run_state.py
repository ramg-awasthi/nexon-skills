from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .common import RUN_STAGES, RUN_STATUS_VALUES, STAGE_STATUS_VALUES, read_json, write_json


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_state(path: Path, *, run_id: str, provider: str, run_mode: str, source_identity: str) -> dict[str, Any]:
    state = {
        "run_id": run_id,
        "provider": provider,
        "run_mode": run_mode,
        "source_identity": source_identity,
        "run_status": "created",
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "stages": {
            stage: {
                "status": "pending",
                "attempts": 0,
                "started_at": None,
                "completed_at": None,
                "counts": {},
                "artifacts": [],
                "failure_code": None,
                "retryable": False,
            }
            for stage in RUN_STAGES
        },
    }
    write_json(path, state)
    return state


def update_stage(
    path: Path,
    stage: str,
    status: str,
    *,
    counts: dict[str, int] | None = None,
    artifacts: list[str] | None = None,
    failure_code: str | None = None,
    retryable: bool = False,
) -> dict[str, Any]:
    if stage not in RUN_STAGES:
        raise ValueError(f"Unknown run stage: {stage}")
    if status not in STAGE_STATUS_VALUES:
        raise ValueError(f"Unknown stage status: {status}")
    state = read_json(path)
    record = state["stages"][stage]
    now = utc_now()
    if status == "running":
        record["attempts"] += 1
        record["started_at"] = now
        state["run_status"] = "running"
    if status in {"completed", "failed", "skipped"}:
        record["completed_at"] = now
    record["status"] = status
    record["counts"] = counts or record.get("counts", {})
    record["artifacts"] = artifacts or record.get("artifacts", [])
    record["failure_code"] = failure_code
    record["retryable"] = bool(retryable)
    if status == "failed":
        state["run_status"] = "failed"
    state["updated_at"] = now
    write_json(path, state)
    return state


def finalize_state(path: Path, status: str) -> dict[str, Any]:
    if status not in RUN_STATUS_VALUES:
        raise ValueError(f"Unknown run status: {status}")
    state = read_json(path)
    if status == "completed":
        failed = [name for name, stage in state["stages"].items() if stage["status"] == "failed"]
        if failed:
            raise RuntimeError(f"Cannot complete a run with failed stages: {failed}")
    state["run_status"] = status
    state["updated_at"] = utc_now()
    state["completed_at"] = utc_now() if status in {"completed", "failed"} else None
    write_json(path, state)
    return state
