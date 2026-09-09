"""Scout adapter wrappers (Work Exchange local/stub implementations)."""

from __future__ import annotations

from flop_work_exchange.adapters.scout import (
    LocalScoutAdapter,
    StubScoutAdapter,
    assess_scout_sqlite,
    candidates_from_records,
    candidates_payload,
    cap_worker_candidates,
    run_sqlite_readonly,
)

__all__ = [
    "LocalScoutAdapter",
    "StubScoutAdapter",
    "assess_scout_sqlite",
    "candidates_from_records",
    "candidates_payload",
    "cap_worker_candidates",
    "run_sqlite_readonly",
]
