"""Adapter interfaces for Scout, Bench, Router, and Sentinel.

These are adapters only. They do not reimplement the sibling agents. Stubs
work fully offline. Local Scout/Router/Sentinel adapters reuse Work Exchange
wrappers (same sibling CLI/library contracts). Foundry Bench is bounty-domain:
passive evidence checks by default, or ``flop-bench verify`` in local mode.
"""

from __future__ import annotations

from flop_code_bounty_foundry.adapters.bench import BountyBenchAdapter, LocalBountyBenchAdapter
from flop_code_bounty_foundry.adapters.factory import AdapterBundle, resolve_adapters
from flop_code_bounty_foundry.adapters.router import LocalRouterAdapter, StubRouterAdapter
from flop_code_bounty_foundry.adapters.scout import LocalScoutAdapter, StubScoutAdapter
from flop_code_bounty_foundry.adapters.sentinel import LocalSentinelAdapter, StubSentinelAdapter
from flop_code_bounty_foundry.adapters.tclk import StubTclkAdapter

__all__ = [
    "AdapterBundle",
    "BountyBenchAdapter",
    "LocalBountyBenchAdapter",
    "LocalRouterAdapter",
    "LocalScoutAdapter",
    "LocalSentinelAdapter",
    "StubRouterAdapter",
    "StubScoutAdapter",
    "StubSentinelAdapter",
    "StubTclkAdapter",
    "resolve_adapters",
]
