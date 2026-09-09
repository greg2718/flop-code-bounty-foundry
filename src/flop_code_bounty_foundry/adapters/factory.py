"""Resolve stub vs local sibling adapters from AdapterConfig.

Stubs remain the default. Local adapters fail closed when backends are missing;
they never silently become stubs while still being labeled live.

Scout, Router, and Sentinel wrappers live in flop-work-exchange and are reused
here so Foundry does not fork-copy those sibling adapters. Foundry Bench is
bounty-domain: passive evidence-bundle checks (stub) or ``flop-bench verify``
against the bounty spec (local).
"""

from __future__ import annotations

from dataclasses import dataclass

from flop_work_exchange.adapters.bench import LocalBenchAdapter, StubBenchAdapter
from flop_work_exchange.adapters.factory import (
    first_existing_file,
    resolve_bench_argv,
)
from flop_work_exchange.adapters.factory import resolve_adapters as resolve_wx_adapters
from flop_work_exchange.adapters.router import LocalRouterAdapter, StubRouterAdapter
from flop_work_exchange.adapters.scout import LocalScoutAdapter, StubScoutAdapter
from flop_work_exchange.adapters.sentinel import LocalSentinelAdapter, StubSentinelAdapter
from flop_work_exchange.config import AdapterConfig

from flop_code_bounty_foundry.adapters.bench import (
    BountyBenchAdapter,
    BountyLookup,
    LocalBountyBenchAdapter,
)


@dataclass(frozen=True)
class AdapterBundle:
    scout: StubScoutAdapter | LocalScoutAdapter
    bench: BountyBenchAdapter | LocalBountyBenchAdapter
    router: StubRouterAdapter | LocalRouterAdapter
    sentinel: StubSentinelAdapter | LocalSentinelAdapter
    exchange_bench: StubBenchAdapter | LocalBenchAdapter


def resolve_adapters(
    config: AdapterConfig,
    *,
    bounty_lookup: BountyLookup | None = None,
) -> AdapterBundle:
    wx_bundle = resolve_wx_adapters(config)
    return AdapterBundle(
        scout=wx_bundle.scout,
        bench=_resolve_foundry_bench(config, bounty_lookup=bounty_lookup),
        router=wx_bundle.router,
        sentinel=wx_bundle.sentinel,
        exchange_bench=StubBenchAdapter(),
    )


def _resolve_foundry_bench(
    config: AdapterConfig,
    *,
    bounty_lookup: BountyLookup | None,
) -> BountyBenchAdapter | LocalBountyBenchAdapter:
    if config.bench_mode != "local":
        return BountyBenchAdapter(
            lookup=bounty_lookup,
            allow_local_exec=config.bench_allow_local_exec,
        )
    return LocalBountyBenchAdapter(
        argv=resolve_bench_argv(config),
        allow_local_exec=config.bench_allow_local_exec,
        timeout_seconds=config.timeout_seconds,
        lookup=bounty_lookup,
    )


__all__ = [
    "AdapterBundle",
    "first_existing_file",
    "resolve_adapters",
    "resolve_bench_argv",
]
