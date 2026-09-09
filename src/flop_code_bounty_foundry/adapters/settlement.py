"""Paper settlement adapters. Live rails raise NotLiveError."""

from __future__ import annotations

from flop_work_exchange.adapters.settlement import PaperSettlement, TestnetSettlement

__all__ = ["PaperSettlement", "TestnetSettlement"]
