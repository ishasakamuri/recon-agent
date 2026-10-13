"""Run state on disk plus an append-only audit log.

A run moves through: DRAFTED -> IN_REVIEW -> FINAL. Only the review step can
move it to FINAL; nothing the agent does can.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from .loader import Record
from .matcher import Match, ReconException

DRAFTED, IN_REVIEW, FINAL = "DRAFTED", "IN_REVIEW", "FINAL"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class RunState:
    def __init__(self, run_dir: Path, data: dict):
        self.dir = Path(run_dir)
        self.data = data

    # --- construction / persistence -------------------------------------
    @classmethod
    def create(cls, run_dir, *, ledger, statement, matches, exceptions, inputs, config) -> "RunState":
        run_dir = Path(run_dir)
        if (run_dir / "run.json").exists():
            raise FileExistsError(f"{run_dir} already holds a run; pick a new --out directory")
        run_dir.mkdir(parents=True, exist_ok=True)
        state = cls(run_dir, {
            "status": DRAFTED,
            "created_at": now(),
            "inputs": inputs,
            "config": config,
            "ledger": [r.to_dict() for r in ledger],
            "statement": [r.to_dict() for r in statement],
            "matches": [asdict(m) for m in matches],
            "exceptions": [e.to_dict() for e in exceptions],
            "summary": {"draft": None, "author": None, "approved": None},
            "agent": {},
        })
        state.save()
        state.log("run_created", inputs=inputs, matches=len(matches), exceptions=len(exceptions))
        return state

    @classmethod
    def load(cls, run_dir) -> "RunState":
        path = Path(run_dir) / "run.json"
        if not path.exists():
            raise FileNotFoundError(f"no run found at {run_dir}")
        return cls(Path(run_dir), json.loads(path.read_text(encoding="utf-8")))

    def save(self) -> None:
        tmp = self.dir / "run.json.tmp"
        tmp.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
        tmp.replace(self.dir / "run.json")

    def log(self, event: str, **fields) -> None:
        with open(self.dir / "audit_log.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": now(), "event": event, **fields}) + "\n")

    # --- accessors --------------------------------------------------------
    @property
    def status(self) -> str:
        return self.data["status"]

    @property
    def exceptions(self) -> list[dict]:
        return self.data["exceptions"]

    def exception(self, ex_id: str) -> dict | None:
        return next((e for e in self.exceptions if e["id"] == ex_id), None)

    def record(self, rec_id: str) -> Record:
        pool = self.data["ledger"] if rec_id.startswith("L-") else self.data["statement"]
        return Record.from_dict(next(r for r in pool if r["id"] == rec_id))

    def matches(self) -> list[Match]:
        return [Match(**m) for m in self.data["matches"]]

    def exception_objs(self) -> list[ReconException]:
        return [ReconException(**e) for e in self.exceptions]
