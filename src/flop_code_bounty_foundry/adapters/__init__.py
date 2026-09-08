"""Adapter stubs for Scout, Bench, Router, and Sentinel.

These are interfaces and offline stubs only. They do not reimplement the sibling
agents. Later wiring can call the real local packages without changing Foundry
orchestration.
"""

from __future__ import annotations

from flop_work_exchange.adapters.router import StubRouterAdapter
from flop_work_exchange.adapters.scout import StubScoutAdapter
from flop_work_exchange.adapters.sentinel import StubSentinelAdapter
from flop_work_exchange.adapters.tclk import StubTclkAdapter

from flop_code_bounty_foundry.adapters.bench import BountyBenchAdapter

__all__ = [
    "BountyBenchAdapter",
    "StubRouterAdapter",
    "StubScoutAdapter",
    "StubSentinelAdapter",
    "StubTclkAdapter",
]
