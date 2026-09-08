from __future__ import annotations

from pathlib import Path

import pytest
from flop_work_exchange.policy import classify_operator_relationship

from flop_code_bounty_foundry.constants import (
    BENCH_DID,
    FOUNDRY_DID,
    ROUTER_DID,
    SCOUT_DID,
    TOURNAMENT_DID,
)
from flop_code_bounty_foundry.exceptions import PolicyError, SafetyError
from tests.helpers import complete_to_verified, docs_spec, fresh_dids, make_foundry


def test_family_dids_are_same_operator() -> None:
    from flop_code_bounty_foundry.constants import KNOWN_FAMILY_DIDS

    assert classify_operator_relationship(SCOUT_DID, BENCH_DID) == "same_operator"
    assert classify_operator_relationship(SCOUT_DID, ROUTER_DID) == "same_operator"
    assert (
        classify_operator_relationship(
            SCOUT_DID, FOUNDRY_DID, known_family_dids=KNOWN_FAMILY_DIDS
        )
        == "same_operator"
    )
    assert (
        classify_operator_relationship(
            FOUNDRY_DID, TOURNAMENT_DID, known_family_dids=KNOWN_FAMILY_DIDS
        )
        == "same_operator"
    )


def test_family_plus_outsider_is_related() -> None:
    outsider = fresh_dids(1)[0]
    assert classify_operator_relationship(SCOUT_DID, outsider) == "related"


def test_same_operator_deal_does_not_mint_independent_reputation(tmp_path: Path) -> None:
    foundry = make_foundry(tmp_path)
    foundry.credit_paper(SCOUT_DID, "20")
    bounty = foundry.create_bounty(sponsor_did=SCOUT_DID, spec=docs_spec())
    foundry.assign(bounty.bounty_id, implementer_did=BENCH_DID, price_flop="8")
    stored = foundry.store.load_bounty(bounty.bounty_id)
    assert stored.implementer_relationship == "same_operator"
    assert stored.implementer_independent_reputation_eligible is False
    assert stored.implementer_independent_fee_volume_eligible is False
    from flop_work_exchange.canonical import result_hash_for

    from tests.helpers import write_passing_evidence

    evidence = write_passing_evidence(tmp_path)
    foundry.submit_work(
        bounty.bounty_id,
        artifact_hash=result_hash_for("paper"),
        evidence_bundle_path=evidence,
    )
    foundry.verify(bounty.bounty_id)
    foundry.settle(bounty.bounty_id)
    seller = foundry.store.load_profile(BENCH_DID)
    assert seller.independent_completed_jobs == 0
    assert seller.independent_fee_volume_micro == 0
    assert seller.same_operator_completed_jobs == 1
    claims = seller.public_claims()
    assert claims["independent_completed_jobs"] == 0
    assert claims["independent_fee_volume_micro"] == 0


def test_cannot_relabel_family_as_independent(tmp_path: Path) -> None:
    foundry = make_foundry(tmp_path)
    foundry.credit_paper(SCOUT_DID, "20")
    bounty = foundry.create_bounty(sponsor_did=SCOUT_DID, spec=docs_spec(budget_flop="1"))
    with pytest.raises(SafetyError, match="same-operator"):
        foundry.assign(
            bounty.bounty_id,
            implementer_did=BENCH_DID,
            price_flop="1",
            relationship="independent",
        )


def test_self_deal_blocked_by_default(tmp_path: Path) -> None:
    foundry = make_foundry(tmp_path)
    sponsor = fresh_dids(1)[0]
    foundry.credit_paper(sponsor, "20")
    bounty = foundry.create_bounty(sponsor_did=sponsor, spec=docs_spec(budget_flop="1"))
    with pytest.raises(PolicyError, match="self-deals"):
        foundry.assign(bounty.bounty_id, implementer_did=sponsor, price_flop="1")


def test_implementer_cannot_review_own_work(tmp_path: Path) -> None:
    foundry, bounty_id, _sponsor, implementer = complete_to_verified(tmp_path)
    # verified already; need work-submitted bounty
    foundry = make_foundry(tmp_path / "self-review")
    sponsor, implementer = fresh_dids(2)
    foundry.credit_paper(sponsor, "20")
    bounty = foundry.create_bounty(sponsor_did=sponsor, spec=docs_spec())
    foundry.assign(bounty.bounty_id, implementer_did=implementer, price_flop="8")
    from flop_work_exchange.canonical import result_hash_for

    from tests.helpers import write_passing_evidence

    evidence = write_passing_evidence(tmp_path / "self-review")
    foundry.submit_work(
        bounty.bounty_id,
        artifact_hash=result_hash_for("paper"),
        evidence_bundle_path=evidence,
    )
    with pytest.raises(PolicyError, match="cannot review"):
        foundry.request_review(bounty.bounty_id, reviewer_did=implementer, price_flop="1")


def test_circular_counterparties_flag_wash_risk(tmp_path: Path) -> None:
    foundry = make_foundry(tmp_path)
    a, b = fresh_dids(2)
    foundry.credit_paper(a, "40")
    foundry.credit_paper(b, "40")
    from flop_work_exchange.canonical import result_hash_for

    from tests.helpers import write_passing_evidence

    spec = docs_spec(budget_flop="8")
    bounty1 = foundry.create_bounty(sponsor_did=a, spec=spec)
    foundry.assign(
        bounty1.bounty_id, implementer_did=b, price_flop="8", relationship="independent"
    )
    evidence1 = write_passing_evidence(tmp_path / "one")
    foundry.submit_work(
        bounty1.bounty_id,
        artifact_hash=result_hash_for("paper"),
        evidence_bundle_path=evidence1,
    )
    foundry.verify(bounty1.bounty_id)
    foundry.settle(bounty1.bounty_id)

    bounty2 = foundry.create_bounty(sponsor_did=b, spec=docs_spec(budget_flop="8"))
    foundry.assign(
        bounty2.bounty_id, implementer_did=a, price_flop="8", relationship="independent"
    )
    stored = foundry.store.load_bounty(bounty2.bounty_id)
    assert stored.implementer_wash_risk is True
    assert stored.implementer_independent_reputation_eligible is False
    evidence2 = write_passing_evidence(tmp_path / "two")
    foundry.submit_work(
        bounty2.bounty_id,
        artifact_hash=result_hash_for("paper"),
        evidence_bundle_path=evidence2,
    )
    foundry.verify(bounty2.bounty_id)
    foundry.settle(bounty2.bounty_id)
    profile_a = foundry.store.load_profile(a)
    profile_b = foundry.store.load_profile(b)
    assert profile_a.wash_flags >= 1
    assert profile_a.independent_completed_jobs == 0
    assert profile_b.independent_completed_jobs == 1
