from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from flop_work_exchange.adapters.settlement import TestnetSettlement
from flop_work_exchange.amounts import micro_to_flop_string, parse_flop_to_micro
from flop_work_exchange.canonical import result_hash_for

from flop_code_bounty_foundry.config import FeeSchedule, config_from_mapping, load_config
from flop_code_bounty_foundry.constants import BENCH_FEE_ACCOUNT, FOUNDRY_FEE_ACCOUNT
from flop_code_bounty_foundry.exceptions import NotLiveError, ValidationError
from tests.helpers import docs_spec, fresh_dids, make_foundry, write_passing_evidence


def test_parse_flop_exact_micro_no_float() -> None:
    assert parse_flop_to_micro("12") == 12_000_000
    assert parse_flop_to_micro("12.5") == 12_500_000
    assert micro_to_flop_string(12_500_000) == "12.5"
    with pytest.raises(ValidationError):
        parse_flop_to_micro("12.1234567")


def test_testnet_settlement_raises() -> None:
    rail = TestnetSettlement()
    with pytest.raises(NotLiveError, match="not live"):
        rail.credit("did:key:z6Mk", 1, "nope")


def test_foundry_testnet_backend_cannot_credit(tmp_path: Path) -> None:
    foundry = make_foundry(tmp_path, backend="testnet")
    with pytest.raises(NotLiveError):
        foundry.credit_paper("account", "1")


def test_non_paper_payment_mode_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="payment_mode"):
        config_from_mapping(tmp_path, {"payment_mode": "live", "fees": {}})


def test_yaml_fee_config(tmp_path: Path) -> None:
    yaml_path = tmp_path / "fees.yaml"
    yaml_path.write_text(
        yaml.safe_dump(
            {
                "payment_mode": "paper",
                "fees": {
                    "application_micro": 1,
                    "author_micro": 2,
                    "management_micro": 3,
                    "bench_validation_micro": 4,
                    "reviewer_micro": 5,
                    "adapter_publish_micro": 6,
                },
            }
        ),
        encoding="utf-8",
    )
    loaded = load_config(tmp_path, yaml_path)
    assert loaded.fees.application_micro == 1
    assert loaded.payment_mode == "paper"


def test_multi_party_fee_split(tmp_path: Path) -> None:
    fees = FeeSchedule(
        application_micro=100_000,
        author_micro=200_000,
        management_micro=250_000,
        bench_validation_micro=50_000,
        reviewer_micro=1_000_000,
        adapter_publish_micro=0,
    )
    foundry = make_foundry(tmp_path, fees=fees)
    sponsor, implementer, reviewer, author = fresh_dids(4)
    foundry.credit_paper(sponsor, "20")
    spec = docs_spec(author_did=author, budget_flop="8")
    bounty = foundry.create_bounty(sponsor_did=sponsor, spec=spec)
    foundry.assign(
        bounty.bounty_id,
        implementer_did=implementer,
        price_flop="8",
        relationship="independent",
    )
    evidence = write_passing_evidence(tmp_path)
    foundry.submit_work(
        bounty.bounty_id,
        artifact_hash=result_hash_for("paper"),
        evidence_bundle_path=evidence,
    )
    foundry.request_review(
        bounty.bounty_id,
        reviewer_did=reviewer,
        price_flop="1",
        relationship="independent",
        verdict="APPROVE",
    )
    foundry.verify(bounty.bounty_id)
    bundle, _path = foundry.settle(bounty.bounty_id)
    roles = {leg.role: leg.amount_micro for leg in bundle.legs}
    assert roles["author"] == 200_000
    assert roles["implementer"] == 8_000_000
    assert roles["reviewer"] == 1_000_000
    assert roles["bench_validation"] == 50_000
    assert roles["application"] == 350_000
    assert foundry.exchange.paper_balance(author) == 200_000
    assert foundry.exchange.paper_balance(implementer) == 8_000_000
    assert foundry.exchange.paper_balance(reviewer) == 1_000_000
    assert foundry.exchange.paper_balance(BENCH_FEE_ACCOUNT) == 50_000
    assert foundry.exchange.paper_balance(FOUNDRY_FEE_ACCOUNT) == 350_000
    # 20 FLOP seed minus author 0.2, impl 8, reviewer 1, bench 0.05, application 0.35
    assert foundry.exchange.paper_balance(sponsor) == 20_000_000 - 9_600_000
