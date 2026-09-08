from __future__ import annotations

from pathlib import Path

from flop_work_exchange.canonical import result_hash_for

from flop_code_bounty_foundry.config import FeeSchedule, FoundryConfig, PolicyConfig
from flop_code_bounty_foundry.foundry import CodeBountyFoundry
from flop_code_bounty_foundry.identity import create_ephemeral_party, ensure_test_identity
from flop_code_bounty_foundry.models import BountySpec


def make_foundry(
    tmp_path: Path,
    *,
    fees: FeeSchedule | None = None,
    policy: PolicyConfig | None = None,
    backend: str = "paper",
) -> CodeBountyFoundry:
    config = FoundryConfig(
        state_dir=tmp_path,
        settlement_backend=backend,  # type: ignore[arg-type]
        fees=fees or FeeSchedule(),
        policy=policy or PolicyConfig(),
    )
    foundry = CodeBountyFoundry(config)
    ensure_test_identity(tmp_path)
    return foundry


def fresh_dids(count: int = 2) -> tuple[str, ...]:
    dids: list[str] = []
    for label in ("a", "b", "c", "d", "e")[:count]:
        _key, did = create_ephemeral_party(label)
        dids.append(did)
    return tuple(dids)


def docs_spec(*, author_did: str | None = None, budget_flop: str = "8") -> BountySpec:
    payload: dict[str, object] = {
        "title": "Document paper settlement",
        "bounty_type": "improve_documentation",
        "summary": "Write a README that states paper settlement rules.",
        "budget_flop": budget_flop,
        "acceptance": {
            "summary": "README exists and mentions paper.",
            "items": ["README.md exists", "mentions paper"],
            "required_artifacts": ["README.md"],
        },
        "bench": {
            "schema_version": "flop-bench.test-spec.v0.1",
            "claim_id": "test-docs",
            "hypothesis": "README mentions paper.",
            "requested_capabilities": ["file-existence", "utf8-text-containment"],
            "mode": "passive",
            "procedure": [
                {"adapter": "file_exists", "path": "README.md"},
                {"adapter": "text_contains", "path": "README.md", "text": "paper"},
            ],
            "assertions": [{"expect": "all steps pass"}],
            "failure_conditions": ["missing README"],
            "provenance": {"source": "test"},
        },
    }
    if author_did:
        payload["author_did"] = author_did
    return BountySpec.from_dict(payload)


def write_passing_evidence(root: Path) -> Path:
    evidence = root / "evidence"
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence / "README.md").write_text(
        "paper settlement; payment_mode=paper; operator_relationship recorded.\n",
        encoding="utf-8",
    )
    return evidence


def complete_to_verified(
    tmp_path: Path,
    *,
    with_review: bool = False,
    with_author: bool = False,
    relationship: str = "independent",
    foundry: CodeBountyFoundry | None = None,
) -> tuple[CodeBountyFoundry, str, str, str]:
    foundry = foundry or make_foundry(tmp_path)
    sponsor, implementer, reviewer, author = fresh_dids(4)
    foundry.credit_paper(sponsor, "20")
    spec = docs_spec(author_did=author if with_author else None)
    bounty = foundry.create_bounty(sponsor_did=sponsor, spec=spec)
    foundry.assign(
        bounty.bounty_id,
        implementer_did=implementer,
        price_flop="8",
        relationship=relationship,
    )
    evidence = write_passing_evidence(tmp_path)
    foundry.submit_work(
        bounty.bounty_id,
        artifact_hash=result_hash_for("paper"),
        evidence_bundle_path=evidence,
    )
    if with_review:
        foundry.request_review(
            bounty.bounty_id,
            reviewer_did=reviewer,
            price_flop="1",
            relationship=relationship,
            verdict="APPROVE",
            review_text="Looks good.",
        )
    foundry.verify(bounty.bounty_id)
    return foundry, bounty.bounty_id, sponsor, implementer
