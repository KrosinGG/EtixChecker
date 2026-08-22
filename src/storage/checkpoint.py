"""Crash-safe checkpoint management for resumable runs."""

from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.domain.models import CheckResult, Show
from src.utils.logger import LOGGER

RUNS_ROOT = Path("runs")


def _atomic_write_json(path: Path, data: Dict[str, Any]) -> None:
    """Write JSON file atomically using a temporary file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(f".tmp_{int(time.time() * 1000)}")
    temp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(path)


def compute_csv_fingerprint(shows: List[Show]) -> str:
    """Compute sha256 fingerprint for the list of shows."""
    raw = "|".join(f"{s.name}:{s.url}:{s.target_total}" for s in shows)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


class RunContext:
    """State manager for a single check run."""

    def __init__(
        self,
        shows: List[Show],
        run_id: Optional[str] = None,
        runs_dir: Path = RUNS_ROOT,
    ) -> None:
        self.shows = shows
        self.runs_dir = runs_dir
        self.run_id = run_id or f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        self.run_folder = self.runs_dir / self.run_id
        self.checkpoint_file = self.run_folder / "checkpoint.json"
        self.fingerprint = compute_csv_fingerprint(shows)

        self.pending_ids: List[str] = [s.show_id for s in shows]
        self.inflight_ids: List[str] = []
        self.done_results: Dict[str, Dict[str, Any]] = {}

    @classmethod
    def find_last_active_checkpoint(cls, runs_dir: Path = RUNS_ROOT) -> Optional[Path]:
        """Find the latest unfinished checkpoint if exists."""
        if not runs_dir.exists():
            return None
        candidate_files = list(runs_dir.glob("*/checkpoint.json"))
        if not candidate_files:
            return None
        candidate_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return candidate_files[0]

    @classmethod
    def load_from_checkpoint(cls, checkpoint_path: Path, shows: List[Show]) -> Optional["RunContext"]:
        """Restore run context from an existing checkpoint."""
        try:
            data = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            run_id = data.get("run_id")
            ctx = cls(shows=shows, run_id=run_id, runs_dir=checkpoint_path.parent.parent)

            saved_fp = data.get("fingerprint")
            if saved_fp != ctx.fingerprint:
                LOGGER.warning("Checkpoint fingerprint mismatch with current shows.csv!")

            ctx.done_results = data.get("done_results", {})
            done_set = set(ctx.done_results.keys())

            ctx.pending_ids = [s.show_id for s in shows if s.show_id not in done_set]
            ctx.inflight_ids = []
            LOGGER.info(
                f"Resumed run '{run_id}': {len(ctx.done_results)} completed, {len(ctx.pending_ids)} remaining."
            )
            return ctx
        except Exception as exc:
            LOGGER.error(f"Failed to resume checkpoint: {exc}")
            return None

    def mark_inflight(self, show_id: str) -> None:
        """Move show from pending to inflight."""
        if show_id in self.pending_ids:
            self.pending_ids.remove(show_id)
        if show_id not in self.inflight_ids:
            self.inflight_ids.append(show_id)
        self.save_checkpoint()

    def commit_done(self, result: CheckResult) -> None:
        """Mark show as completed and persist result."""
        if result.show_id in self.inflight_ids:
            self.inflight_ids.remove(result.show_id)
        if result.show_id in self.pending_ids:
            self.pending_ids.remove(result.show_id)

        self.done_results[result.show_id] = result.to_report_row()
        self.save_checkpoint()

    def save_checkpoint(self) -> None:
        """Persist current state to checkpoint.json."""
        payload = {
            "run_id": self.run_id,
            "version": "2.0.0",
            "fingerprint": self.fingerprint,
            "total_shows": len(self.shows),
            "done_count": len(self.done_results),
            "pending_count": len(self.pending_ids),
            "done_results": self.done_results,
            "updated_at": datetime.now().isoformat(),
        }
        _atomic_write_json(self.checkpoint_file, payload)

    def complete_run(self) -> None:
        """Remove active checkpoint file upon successful run completion."""
        try:
            if self.checkpoint_file.exists():
                self.checkpoint_file.unlink()
                LOGGER.info(f"Run '{self.run_id}' completed. Checkpoint cleared.")
        except Exception as exc:
            LOGGER.warning(f"Error removing checkpoint file: {exc}")
