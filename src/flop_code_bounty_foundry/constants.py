from __future__ import annotations

from pathlib import Path

from flop_work_exchange.constants import (
    BENCH_DID,
    KNOWN_FAMILY_AGENTS,
    KNOWN_FAMILY_DIDS as WORK_EXCHANGE_FAMILY_DIDS,
    ROUTER_DID,
    SCOUT_DID,
    SENTINEL_DID,
)
from flop_work_exchange.exceptions import IsolationError, SafetyError

DEFAULT_PRODUCTION_STATE = Path.home() / ".flop_agents" / "code-bounty-foundry"
FOUNDRY_OPERATOR_GROUP = "local-flop-agent-family"

# Example currently minted production DIDs (common-control disclosure). Loading
# identity.json accepts any valid Ed25519 did:key; these are not exclusive.
FOUNDRY_DID = "did:key:z6MkrqX5nYL7wskGHASzD4JKw4P2Pu3wGnxWCTJpdnwXeTLD"
TOURNAMENT_DID = "did:key:z6MkjMGSFqV87ZbYcCZJPFBnXQm4H48w7gBSJXxEzRimdkpW"

KNOWN_FAMILY_DIDS: frozenset[str] = frozenset(
    {*WORK_EXCHANGE_FAMILY_DIDS, FOUNDRY_DID, TOURNAMENT_DID}
)

SCOUT_STATE = Path.home() / ".flop_agents" / "scout"
BENCH_STATE = Path.home() / ".flop_agents" / "bench"
ROUTER_STATE = Path.home() / ".flop_agents" / "router"
SENTINEL_STATE = Path.home() / ".flop_agents" / "sentinel"
WORK_EXCHANGE_STATE = Path.home() / ".flop_agents" / "work-exchange"
TOURNAMENT_STATE = Path.home() / ".flop_agents" / "capability-tournament"
LEGACY_SCOUT_STATE = Path.home() / ".flop_scout"

FOUNDRY_FEE_ACCOUNT = "foundry-management-fees"
BENCH_FEE_ACCOUNT = "bench-validation-fees"
ADAPTER_PUBLISH_ACCOUNT = "adapter-publish-fees"

BOUNTY_TYPES: tuple[str, ...] = (
    "add_feature",
    "reproduce_bug",
    "write_test",
    "review_pull_request",
    "improve_documentation",
    "build_adapter",
    "create_deployment_configuration",
    "test_protocol_conformance",
)


def _path_overlaps(left: Path, right: Path) -> bool:
    resolved_left = left.expanduser().resolve(strict=False)
    resolved_right = right.expanduser().resolve(strict=False)
    if resolved_left == resolved_right:
        return True
    try:
        return resolved_left.is_relative_to(resolved_right) or resolved_right.is_relative_to(
            resolved_left
        )
    except ValueError:
        return False


def sibling_state_dirs() -> tuple[Path, ...]:
    return (
        SCOUT_STATE,
        BENCH_STATE,
        ROUTER_STATE,
        SENTINEL_STATE,
        WORK_EXCHANGE_STATE,
        TOURNAMENT_STATE,
        LEGACY_SCOUT_STATE,
    )


def assert_isolated_state_dir(state_dir: Path) -> Path:
    resolved = state_dir.expanduser().resolve(strict=False)
    if state_dir.expanduser().is_symlink() or resolved.is_symlink():
        raise IsolationError("state directory must not be a symlink")
    for sibling in sibling_state_dirs():
        if _path_overlaps(resolved, sibling):
            raise IsolationError(f"state directory overlaps sibling agent state: {sibling}")
    return resolved


def assert_not_production_auto_init(state_dir: Path) -> None:
    resolved = state_dir.expanduser().resolve(strict=False)
    expected = DEFAULT_PRODUCTION_STATE.expanduser().resolve(strict=False)
    if resolved == expected:
        raise SafetyError(
            "refusing to auto-create identity in the production state directory; "
            "run identity init with explicit confirmation"
        )


__all__ = [
    "ADAPTER_PUBLISH_ACCOUNT",
    "BENCH_DID",
    "BENCH_FEE_ACCOUNT",
    "BENCH_STATE",
    "BOUNTY_TYPES",
    "DEFAULT_PRODUCTION_STATE",
    "FOUNDRY_DID",
    "FOUNDRY_FEE_ACCOUNT",
    "FOUNDRY_OPERATOR_GROUP",
    "KNOWN_FAMILY_AGENTS",
    "KNOWN_FAMILY_DIDS",
    "ROUTER_DID",
    "ROUTER_STATE",
    "SCOUT_DID",
    "SCOUT_STATE",
    "SENTINEL_DID",
    "SENTINEL_STATE",
    "TOURNAMENT_DID",
    "TOURNAMENT_STATE",
    "WORK_EXCHANGE_STATE",
    "assert_isolated_state_dir",
    "assert_not_production_auto_init",
    "sibling_state_dirs",
]
