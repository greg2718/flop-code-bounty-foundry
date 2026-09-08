from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml
from flop_work_exchange.amounts import parse_micro
from flop_work_exchange.canonical import atomic_write_json
from flop_work_exchange.config import PolicyConfig as PolicyConfig

from flop_code_bounty_foundry.constants import (
    FOUNDRY_OPERATOR_GROUP,
    KNOWN_FAMILY_DIDS,
    assert_isolated_state_dir,
)
from flop_code_bounty_foundry.exceptions import ValidationError

PaymentMode = Literal["paper"]

DEFAULT_FEES = {
    "application_micro": 100_000,
    "author_micro": 200_000,
    "management_micro": 250_000,
    "bench_validation_micro": 50_000,
    "reviewer_micro": 1_000_000,
    "adapter_publish_micro": 100_000,
}


def _package_data_dir() -> Path:
    return Path(__file__).resolve().parent / "data"


def default_fee_config_path() -> Path:
    return _package_data_dir() / "fees.json"


@dataclass(frozen=True)
class FeeSchedule:
    application_micro: int = 100_000
    author_micro: int = 200_000
    management_micro: int = 250_000
    bench_validation_micro: int = 50_000
    reviewer_micro: int = 1_000_000
    adapter_publish_micro: int = 100_000

    def settle_overhead_micro(self, *, include_author: bool, include_reviewer: bool) -> int:
        total = (
            self.application_micro + self.management_micro + self.bench_validation_micro
        )
        if include_author:
            total += self.author_micro
        if include_reviewer:
            total += self.reviewer_micro
        return total

    def sponsor_settle_total_micro(
        self,
        implementer_micro: int,
        *,
        include_author: bool,
        include_reviewer: bool,
        include_adapter_publish: bool,
    ) -> int:
        total = implementer_micro + self.settle_overhead_micro(
            include_author=include_author, include_reviewer=include_reviewer
        )
        if include_adapter_publish:
            total += self.adapter_publish_micro
        return total


@dataclass(frozen=True)
class FoundryConfig:
    state_dir: Path
    payment_mode: PaymentMode = "paper"
    settlement_backend: Literal["paper", "testnet"] = "paper"
    settlement_execution: Literal["DISABLED"] = "DISABLED"
    tclk_mode: Literal["SIMULATION_ONLY"] = "SIMULATION_ONLY"
    operator_group: str = FOUNDRY_OPERATOR_GROUP
    known_family_dids: frozenset[str] = field(default_factory=lambda: KNOWN_FAMILY_DIDS)
    fees: FeeSchedule = field(default_factory=FeeSchedule)
    policy: PolicyConfig = field(default_factory=PolicyConfig)
    asset: str = "FLOP"
    micro_per_flop: int = 1_000_000
    allow_local_exec: bool = False

    def resolved_state_dir(self) -> Path:
        return assert_isolated_state_dir(self.state_dir)

    def exchange_state_dir(self) -> Path:
        return self.resolved_state_dir() / "work-exchange"


def _require_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValidationError(f"{label} must be an object")
    return value


def _fee_schedule_from_mapping(raw: dict[str, Any]) -> FeeSchedule:
    fees = _require_mapping(raw.get("fees", {}), "fees") if "fees" in raw else dict(DEFAULT_FEES)
    merged = {**DEFAULT_FEES, **fees}
    return FeeSchedule(
        application_micro=parse_micro(merged["application_micro"]),
        author_micro=parse_micro(merged["author_micro"]),
        management_micro=parse_micro(merged["management_micro"]),
        bench_validation_micro=parse_micro(merged["bench_validation_micro"]),
        reviewer_micro=parse_micro(merged["reviewer_micro"]),
        adapter_publish_micro=parse_micro(merged["adapter_publish_micro"]),
    )


def _policy_from_mapping(raw: dict[str, Any]) -> PolicyConfig:
    if "policy" not in raw:
        return PolicyConfig()
    policy = _require_mapping(raw.get("policy"), "policy")
    return PolicyConfig(
        allow_same_operator_deals=bool(policy.get("allow_same_operator_deals", True)),
        same_operator_independent_reputation=bool(
            policy.get("same_operator_independent_reputation", False)
        ),
        same_operator_fee_volume_as_independent=bool(
            policy.get("same_operator_fee_volume_as_independent", False)
        ),
        allow_self_deals=bool(policy.get("allow_self_deals", False)),
        treat_unknown_as_independent=bool(policy.get("treat_unknown_as_independent", False)),
    )


def load_mapping(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    suffix = path.suffix.lower()
    if suffix == ".json":
        loaded = json.loads(text)
    elif suffix in {".yaml", ".yml"}:
        loaded = yaml.safe_load(text)
    elif suffix == ".toml":
        import tomllib

        loaded = tomllib.loads(text)
    else:
        raise ValidationError(f"unsupported config format: {suffix}")
    return _require_mapping(loaded, "config")


def config_from_mapping(state_dir: Path, raw: dict[str, Any]) -> FoundryConfig:
    payment_mode = raw.get("payment_mode", "paper")
    if payment_mode != "paper":
        raise ValidationError('payment_mode must be "paper"; live rails are not available')
    backend = raw.get("settlement_backend", "paper")
    if backend not in {"paper", "testnet"}:
        raise ValidationError('settlement_backend must be "paper" or "testnet"')
    family = raw.get("known_family_dids")
    known = KNOWN_FAMILY_DIDS
    if family is not None:
        if not isinstance(family, list) or not all(isinstance(item, str) for item in family):
            raise ValidationError("known_family_dids must be a list of DID strings")
        known = frozenset(family)
    return FoundryConfig(
        state_dir=state_dir,
        payment_mode="paper",
        settlement_backend="testnet" if backend == "testnet" else "paper",
        fees=_fee_schedule_from_mapping(raw),
        policy=_policy_from_mapping(raw),
        known_family_dids=known,
        operator_group=str(raw.get("operator_group", FOUNDRY_OPERATOR_GROUP)),
        asset=str(raw.get("asset", "FLOP")),
        allow_local_exec=bool(raw.get("allow_local_exec", False)),
    )


def load_config(state_dir: Path, config_path: Path | None = None) -> FoundryConfig:
    path = config_path or default_fee_config_path()
    return config_from_mapping(state_dir, load_mapping(path))


def write_resolved_config(state_dir: Path, config: FoundryConfig) -> None:
    payload = {
        "payment_mode": config.payment_mode,
        "settlement_backend": config.settlement_backend,
        "settlement_execution": config.settlement_execution,
        "tclk_mode": config.tclk_mode,
        "operator_group": config.operator_group,
        "asset": config.asset,
        "micro_per_flop": config.micro_per_flop,
        "allow_local_exec": config.allow_local_exec,
        "known_family_dids": sorted(config.known_family_dids),
        "fees": {
            "application_micro": config.fees.application_micro,
            "author_micro": config.fees.author_micro,
            "management_micro": config.fees.management_micro,
            "bench_validation_micro": config.fees.bench_validation_micro,
            "reviewer_micro": config.fees.reviewer_micro,
            "adapter_publish_micro": config.fees.adapter_publish_micro,
        },
        "policy": {
            "allow_same_operator_deals": config.policy.allow_same_operator_deals,
            "same_operator_independent_reputation": (
                config.policy.same_operator_independent_reputation
            ),
            "same_operator_fee_volume_as_independent": (
                config.policy.same_operator_fee_volume_as_independent
            ),
            "allow_self_deals": config.policy.allow_self_deals,
            "treat_unknown_as_independent": config.policy.treat_unknown_as_independent,
        },
    }
    atomic_write_json(state_dir / "config.json", payload)
