"""Sentinel adapter wrappers (Work Exchange local/stub implementations).

Local mode uses flop_sentinel's real contract: ``import flop_sentinel.policy``,
``Message`` + ``normalize`` + ``detectors.base.run_all(ALL_DETECTORS, …)`` →
``policy.decide`` with typed ``Provenance.UNSIGNED`` for paper artifacts.
Do not use ``getattr(module, "policy")`` after a bare import; do not invent
``Provenance.LOCAL``; do not call ``detect(text)``.
"""

from __future__ import annotations

from flop_work_exchange.adapters.sentinel import (
    LocalSentinelAdapter,
    StubSentinelAdapter,
    collect_sentinel_findings,
    rule_ids_only,
    sanitize_sentinel_reasons,
    verdict_from_sentinel_raw,
)

__all__ = [
    "LocalSentinelAdapter",
    "StubSentinelAdapter",
    "collect_sentinel_findings",
    "rule_ids_only",
    "sanitize_sentinel_reasons",
    "verdict_from_sentinel_raw",
]
