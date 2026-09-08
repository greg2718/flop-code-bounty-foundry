from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from flop_work_exchange.canonical import result_hash_for
from flop_work_exchange.receipts import verify_receipt

from flop_code_bounty_foundry.config import FoundryConfig, PolicyConfig
from flop_code_bounty_foundry.foundry import CodeBountyFoundry
from flop_code_bounty_foundry.identity import create_ephemeral_party, ensure_test_identity
from flop_code_bounty_foundry.models import BountySpec


def _write_demo_evidence(root: Path) -> Path:
    evidence = root / "demo-evidence"
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence / "README.md").write_text(
        "# FLOP Code Bounty Foundry demo\n\n"
        "Paper settlement only. payment_mode=paper. "
        "Work Exchange job with Bench-shaped passive checks.\n",
        encoding="utf-8",
    )
    (evidence / "test_paper_settlement.py").write_text(
        "def test_payment_mode_is_paper() -> None:\n"
        "    assert True  # placeholder passive artifact\n",
        encoding="utf-8",
    )
    (evidence / "conformance.json").write_text(
        '{\n  "checks": {\n    "receipt_schema": true,\n'
        '    "paper_settlement": true,\n    "inert_urls": true\n  }\n}\n',
        encoding="utf-8",
    )
    return evidence


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return digest


def run_demo(state_dir: Path) -> dict[str, Any]:
    """Run one full paper bounty with Bench-shaped PASS and multiple signed receipts."""
    config = FoundryConfig(
        state_dir=state_dir,
        payment_mode="paper",
        settlement_backend="paper",
        policy=PolicyConfig(treat_unknown_as_independent=False, allow_same_operator_deals=True),
    )
    foundry = CodeBountyFoundry(config)
    ensure_test_identity(state_dir)
    _sponsor_key, sponsor_did = create_ephemeral_party("sponsor")
    _author_key, author_did = create_ephemeral_party("author")
    _impl_key, implementer_did = create_ephemeral_party("implementer")
    _rev_key, reviewer_did = create_ephemeral_party("reviewer")
    foundry.credit_paper(sponsor_did, "20", "demo-sponsor-seed")
    evidence = _write_demo_evidence(state_dir)
    readme_sha = _sha256_file(evidence / "README.md")
    spec = BountySpec.from_dict(
        {
            "title": "Improve Foundry paper-settlement docs",
            "bounty_type": "improve_documentation",
            "summary": "Document paper-FLOP settlement and Work Exchange integration.",
            "repo_url": "https://github.com/greg2718/flop-code-bounty-foundry",
            "local_path": ".",
            "budget_flop": "8",
            "author_did": author_did,
            "acceptance": {
                "summary": "README exists and states paper settlement.",
                "items": [
                    "README.md exists",
                    "README mentions paper settlement",
                    "A placeholder test file is present",
                ],
                "required_artifacts": ["README.md", "test_paper_settlement.py"],
            },
            "bench": {
                "schema_version": "flop-bench.test-spec.v0.1",
                "claim_id": "foundry-demo-docs",
                "hypothesis": "The deliverable documents paper settlement.",
                "requested_capabilities": [
                    "file-existence",
                    "utf8-text-containment",
                    "file-sha256",
                ],
                "mode": "passive",
                "procedure": [
                    {"adapter": "file_exists", "path": "README.md"},
                    {"adapter": "file_sha256", "path": "README.md", "sha256": readme_sha},
                    {"adapter": "text_contains", "path": "README.md", "text": "paper"},
                    {"adapter": "file_exists", "path": "test_paper_settlement.py"},
                    {
                        "adapter": "json_path_equals",
                        "path": "conformance.json",
                        "json_path": "checks.paper_settlement",
                        "equals": True,
                    },
                ],
                "assertions": [{"expect": "all steps pass"}],
                "failure_conditions": ["missing README", "does not mention paper"],
                "provenance": {
                    "source": "foundry demo",
                    "repo_url_is_inert_metadata": True,
                },
            },
        }
    )
    bounty = foundry.create_bounty(sponsor_did=sponsor_did, spec=spec)
    bounty = foundry.assign(
        bounty.bounty_id,
        implementer_did=implementer_did,
        price_flop="8",
        relationship="independent",
    )
    artifact_hash = result_hash_for((evidence / "README.md").read_text(encoding="utf-8"))
    bounty = foundry.submit_work(
        bounty.bounty_id,
        artifact_hash=artifact_hash,
        evidence_bundle_path=evidence,
    )
    bounty = foundry.request_review(
        bounty.bounty_id,
        reviewer_did=reviewer_did,
        price_flop="1",
        relationship="independent",
        verdict="APPROVE",
        review_text="Passive Bench checks match the bounty spec. Paper settlement only.",
    )
    bounty = foundry.verify(bounty.bounty_id)
    bundle, bundle_path = foundry.settle(bounty.bounty_id)
    payload = bundle.to_dict()
    verifications = []
    for leg in bundle.legs:
        check = verify_receipt(leg.receipt)
        verifications.append({"role": leg.role, "verification": check})
    implementer_profile = foundry.store.load_profile(implementer_did).public_claims()
    return {
        "ok": True,
        "state_dir": str(state_dir),
        "foundry_did": foundry.foundry_did(),
        "exchange_did": foundry.exchange.exchange_did(),
        "sponsor_did": sponsor_did,
        "author_did": author_did,
        "implementer_did": implementer_did,
        "reviewer_did": reviewer_did,
        "bounty_id": bounty.bounty_id,
        "implementer_job_id": bounty.implementer_job_id,
        "operator_relationship": bounty.implementer_relationship,
        "independent_reputation_eligible": bounty.implementer_independent_reputation_eligible,
        "bench_result": bounty.bench_result,
        "sentinel_status": bounty.sentinel_status,
        "bundle_path": str(bundle_path),
        "receipt_bundle": payload,
        "receipt_roles": [leg.role for leg in bundle.legs],
        "receipt_verifications": verifications,
        "implementer_public_claims": implementer_profile,
        "balances": foundry.balances(),
        "payment_mode": "paper",
        "settlement_status": "simulated",
        "settlement_execution": "DISABLED",
        "urls_are_inert": True,
    }
