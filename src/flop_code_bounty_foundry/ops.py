"""Ops helpers: doctor checks and the paper live-demo bounty path."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from flop_work_exchange.adapters.bench import StubBenchAdapter
from flop_work_exchange.adapters.router import StubRouterAdapter
from flop_work_exchange.adapters.scout import StubScoutAdapter
from flop_work_exchange.adapters.sentinel import StubSentinelAdapter
from flop_work_exchange.canonical import result_hash_for
from flop_work_exchange.config import AdapterConfig, PolicyConfig
from flop_work_exchange.exceptions import AdapterError, IsolationError, ValidationError
from flop_work_exchange.receipts import verify_receipt

from flop_code_bounty_foundry.adapters.bench import BountyBenchAdapter
from flop_code_bounty_foundry.adapters.factory import AdapterBundle, resolve_adapters
from flop_code_bounty_foundry.config import FoundryConfig
from flop_code_bounty_foundry.constants import (
    BENCH_STATE,
    DEFAULT_PRODUCTION_STATE,
    DEFAULT_SCOUT_CANDIDATE_LIMIT,
    LEGACY_SCOUT_STATE,
    MAC_BENCH_REPO,
    MAC_ROUTER_REPO,
    MAC_SCOUT_REPO,
    MAC_SENTINEL_REPO,
    ROUTER_STATE,
    SCOUT_STATE,
    SENTINEL_STATE,
    sibling_state_dirs,
)
from flop_code_bounty_foundry.demo import _sha256_file, _write_demo_evidence
from flop_code_bounty_foundry.foundry import CodeBountyFoundry
from flop_code_bounty_foundry.identity import (
    TEST_IDENTITY_JSON,
    create_ephemeral_party,
    ensure_test_identity,
    load_identity_meta,
)
from flop_code_bounty_foundry.models import BountySpec


def doctor(
    *,
    state_dir: Path | None = None,
    adapter_config: AdapterConfig | None = None,
    config_path: Path | None = None,
) -> dict[str, Any]:
    adapters = adapter_config or AdapterConfig()
    bundle = resolve_adapters(adapters)
    checks: list[dict[str, Any]] = [
        {
            "name": "payment_mode",
            "ok": True,
            "value": "paper",
            "note": "live FLOP rails are not available; faucet/wallet/transfer are out of scope",
        },
        {
            "name": "tclk_mode",
            "ok": True,
            "value": "SIMULATION_ONLY",
        },
        {
            "name": "settlement_execution",
            "ok": True,
            "value": "DISABLED",
        },
        {
            "name": "bench_allow_local_exec",
            "ok": True,
            "value": adapters.bench_allow_local_exec,
            "note": "default false; never pass --allow-local-exec unless explicitly enabled",
        },
        _adapter_check("scout", adapters.scout_mode, bundle.scout.probe()),
        _adapter_check("bench", adapters.bench_mode, bundle.bench.probe()),
        _adapter_check("router", adapters.router_mode, bundle.router.probe()),
        _adapter_check("sentinel", adapters.sentinel_mode, bundle.sentinel.probe()),
        {
            "name": "mac_dev_paths",
            "ok": True,
            "value": {
                "scout": str(MAC_SCOUT_REPO),
                "bench": str(MAC_BENCH_REPO),
                "router": str(MAC_ROUTER_REPO),
                "sentinel": str(MAC_SENTINEL_REPO),
            },
            "exists": {
                "scout": MAC_SCOUT_REPO.exists(),
                "bench": MAC_BENCH_REPO.exists(),
                "router": MAC_ROUTER_REPO.exists(),
                "sentinel": MAC_SENTINEL_REPO.exists(),
            },
        },
        {
            "name": "sibling_state_isolation_rules",
            "ok": True,
            "forbidden": [str(path) for path in sibling_state_dirs()],
            "production_state": str(DEFAULT_PRODUCTION_STATE),
            "note": (
                "Foundry must not use Scout/Bench/Router/Sentinel/Work Exchange state dirs. "
                "Live Bench verify uses a temp --state-dir."
            ),
        },
    ]
    if state_dir is not None:
        checks.append(_isolation_check(state_dir))
        checks.append(_identity_check(state_dir))
        checks.append(_overlap_live_backends(state_dir))
    ok = all(bool(check.get("ok", True)) for check in checks)
    return {
        "ok": ok,
        "payment_mode": "paper",
        "tclk_mode": "SIMULATION_ONLY",
        "settlement_execution": "DISABLED",
        "not_live": [
            "faucet",
            "wallet",
            "token transfer",
            "Technocore payment endpoints",
            "settlement_execution",
        ],
        "adapter_modes": {
            "scout": adapters.scout_mode,
            "bench": adapters.bench_mode,
            "router": adapters.router_mode,
            "sentinel": adapters.sentinel_mode,
        },
        "config_path": str(config_path) if config_path is not None else None,
        "scout_candidate_limit": adapters.scout_candidate_limit,
        "scout_projection_db": (
            str(adapters.scout_projection_db) if adapters.scout_projection_db else None
        ),
        "scout_evidence_jsonl": (
            str(adapters.scout_evidence_jsonl) if adapters.scout_evidence_jsonl else None
        ),
        "scout_sqlite_timeout_seconds": adapters.scout_sqlite_timeout_seconds,
        "scout_max_db_bytes": adapters.scout_max_db_bytes,
        "router_db": str(adapters.router_db) if adapters.router_db else None,
        "router_fixture": str(adapters.router_fixture) if adapters.router_fixture else None,
        "checks": checks,
    }


def run_live_demo(
    state_dir: Path,
    *,
    adapter_config: AdapterConfig | None = None,
    config_path: Path | None = None,
) -> dict[str, Any]:
    """Run one paper bounty, preferring local adapters that actually probe OK.

    Counterparties are ephemeral demo DIDs (not family Scout/Bench/Router IDs) so
    same-operator sibling identities are not presented as independent workers.
    Self-deals stay disabled. Settlement remains paper / simulated.
    """
    requested = adapter_config or AdapterConfig()
    notes: list[str] = []
    mid_run_errors: list[str] = []
    bundle = resolve_adapters(requested)
    scout, notes = _prefer_local(
        "scout", requested.scout_mode, bundle.scout, StubScoutAdapter(), notes
    )
    bench, notes = _prefer_local(
        "bench", requested.bench_mode, bundle.bench, BountyBenchAdapter(), notes
    )
    sentinel, notes = _prefer_local(
        "sentinel", requested.sentinel_mode, bundle.sentinel, StubSentinelAdapter(), notes
    )
    router, notes = _prefer_local(
        "router", requested.router_mode, bundle.router, StubRouterAdapter(), notes
    )
    live_bundle = AdapterBundle(
        scout=scout,
        bench=bench,
        router=router,
        sentinel=sentinel,
        exchange_bench=StubBenchAdapter(),
    )
    config = FoundryConfig(
        state_dir=state_dir,
        payment_mode="paper",
        settlement_backend="paper",
        policy=PolicyConfig(
            treat_unknown_as_independent=False,
            allow_same_operator_deals=True,
            allow_self_deals=False,
        ),
        adapters=requested,
        allow_local_exec=requested.bench_allow_local_exec,
    )
    foundry = CodeBountyFoundry(config, adapters=live_bundle)
    ensure_test_identity(state_dir)
    _sponsor_key, sponsor_did = create_ephemeral_party("sponsor")
    _author_key, author_did = create_ephemeral_party("author")
    _impl_key, implementer_did = create_ephemeral_party("implementer")
    _rev_key, reviewer_did = create_ephemeral_party("reviewer")
    if len({sponsor_did, author_did, implementer_did, reviewer_did}) != 4:
        raise ValidationError("live-demo counterparties must be distinct DIDs")
    foundry.credit_paper(sponsor_did, "20", "demo-sponsor-seed")
    evidence = _write_demo_evidence(state_dir)
    readme_sha = _sha256_file(evidence / "README.md")
    spec = _live_demo_spec(author_did=author_did, readme_sha=readme_sha)
    bounty = foundry.create_bounty(sponsor_did=sponsor_did, spec=spec)
    candidates: list[Any] = []
    if bounty.implementer_job_id:
        try:
            candidates = foundry.exchange.find_candidates(bounty.implementer_job_id)
        except AdapterError as exc:
            if getattr(foundry.adapters.scout, "kind", "") == "local":
                mid_run_errors.append(f"scout: {exc}")
                notes.append(f"scout: local find_candidates failed ({exc}); falling back to stub")
                _swap_scout(foundry, StubScoutAdapter())
                candidates = foundry.exchange.find_candidates(bounty.implementer_job_id)
    try:
        bounty = foundry.assign(
            bounty.bounty_id,
            implementer_did=implementer_did,
            price_flop="8",
            relationship="independent",
        )
    except AdapterError as exc:
        if getattr(foundry.adapters.router, "kind", "") != "local":
            raise
        mid_run_errors.append(f"router: {exc}")
        notes.append(f"router: local plan failed ({exc}); falling back to stub router")
        _swap_router(foundry, StubRouterAdapter())
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
    try:
        bounty = foundry.verify(bounty.bounty_id)
    except AdapterError as exc:
        if getattr(foundry.bench, "kind", "") != "local":
            raise
        mid_run_errors.append(f"bench: {exc}")
        notes.append(f"bench: local verify failed ({exc}); falling back to stub")
        foundry.bench = BountyBenchAdapter()
        foundry.adapters = AdapterBundle(
            scout=foundry.adapters.scout,
            bench=foundry.bench,
            router=foundry.adapters.router,
            sentinel=foundry.adapters.sentinel,
            exchange_bench=foundry.adapters.exchange_bench,
        )
        bounty = foundry.verify(bounty.bounty_id)
    bundle_obj, bundle_path = foundry.settle(bounty.bounty_id)
    payload = bundle_obj.to_dict()
    verifications: list[dict[str, Any]] = []
    for leg in bundle_obj.legs:
        check = verify_receipt(leg.receipt)
        verifications.append({"role": leg.role, "verification": check})
    implementer_profile = foundry.store.load_profile(implementer_did).public_claims()
    limit = requested.scout_candidate_limit or DEFAULT_SCOUT_CANDIDATE_LIMIT
    candidate_dids = [candidate.did for candidate in candidates[:limit]]
    ok = (
        bool(verifications)
        and all(item["verification"].get("ok") for item in verifications)
        and not mid_run_errors
        and bounty.bench_result == "PASS"
    )
    return {
        "ok": ok,
        "state_dir": str(state_dir),
        "config_path": str(config_path) if config_path is not None else None,
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
        "receipt_roles": [leg.role for leg in bundle_obj.legs],
        "receipt_verifications": verifications,
        "implementer_public_claims": implementer_profile,
        "balances": foundry.balances(),
        "payment_mode": "paper",
        "settlement_status": "simulated",
        "settlement_execution": "DISABLED",
        "urls_are_inert": True,
        "candidates_from_scout": candidate_dids,
        "candidates_shown": len(candidate_dids),
        "candidates_limit": limit,
        "adapter_kinds": {
            "scout": getattr(foundry.adapters.scout, "kind", "unknown"),
            "bench": getattr(foundry.bench, "kind", "unknown"),
            "router": getattr(foundry.adapters.router, "kind", "unknown"),
            "sentinel": getattr(foundry.adapters.sentinel, "kind", "unknown"),
        },
        "adapter_notes": notes,
        "adapter_errors": mid_run_errors,
        "not_live": ["faucet", "wallet", "token transfer", "settlement_execution"],
        "anti_wash": {
            "distinct_counterparties": True,
            "allow_self_deals": False,
            "family_dids_not_used_as_workers": True,
        },
    }


def _nested_exchange(foundry: CodeBountyFoundry) -> Any:
    client = foundry.exchange
    return getattr(client, "exchange", client)


def _swap_scout(foundry: CodeBountyFoundry, scout: StubScoutAdapter) -> None:
    foundry.adapters = AdapterBundle(
        scout=scout,
        bench=foundry.adapters.bench,
        router=foundry.adapters.router,
        sentinel=foundry.adapters.sentinel,
        exchange_bench=foundry.adapters.exchange_bench,
    )
    nested = _nested_exchange(foundry)
    nested.scout = scout


def _swap_router(foundry: CodeBountyFoundry, router: StubRouterAdapter) -> None:
    foundry.adapters = AdapterBundle(
        scout=foundry.adapters.scout,
        bench=foundry.adapters.bench,
        router=router,
        sentinel=foundry.adapters.sentinel,
        exchange_bench=foundry.adapters.exchange_bench,
    )
    nested = _nested_exchange(foundry)
    nested.router = router


def _live_demo_spec(*, author_did: str, readme_sha: str) -> BountySpec:
    return BountySpec.from_dict(
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
                "claim_id": "foundry-live-demo-docs",
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
                    "source": "foundry live-demo",
                    "repo_url_is_inert_metadata": True,
                },
            },
        }
    )


def _prefer_local(
    name: str,
    mode: str,
    local_or_stub: Any,
    stub: Any,
    notes: list[str],
) -> tuple[Any, list[str]]:
    if mode != "local":
        notes.append(f"{name}: stub (default offline)")
        return stub, notes
    probe = local_or_stub.probe()
    if probe.get("ok"):
        notes.append(f"{name}: local ({probe.get('note') or 'probe ok'})")
        return local_or_stub, notes
    notes.append(
        f"{name}: local requested but unavailable ({probe.get('error')}); falling back to stub"
    )
    return stub, notes


def _adapter_check(name: str, mode: str, probe: dict[str, Any]) -> dict[str, Any]:
    ok = True if mode == "stub" else bool(probe.get("ok"))
    return {
        "name": f"adapter_{name}",
        "ok": ok,
        "mode": mode,
        "kind": probe.get("kind"),
        "probe": probe,
        "note": (
            "local mode fails closed when the sibling backend is missing; "
            "stub success is never labeled live"
            if mode == "local" and not probe.get("ok")
            else probe.get("note") or probe.get("error")
        ),
    }


def _isolation_check(state_dir: Path) -> dict[str, Any]:
    from flop_code_bounty_foundry.constants import assert_isolated_state_dir

    try:
        resolved = assert_isolated_state_dir(state_dir)
    except IsolationError as exc:
        return {"name": "state_isolation", "ok": False, "error": str(exc)}
    return {"name": "state_isolation", "ok": True, "state_dir": str(resolved)}


def _identity_check(state_dir: Path) -> dict[str, Any]:
    try:
        meta = load_identity_meta(state_dir)
    except (ValidationError, IsolationError) as exc:
        exists = (state_dir.expanduser() / TEST_IDENTITY_JSON).exists()
        return {
            "name": "identity",
            "ok": True,
            "present": exists,
            "note": str(exc) if not exists else str(exc),
        }
    return {
        "name": "identity",
        "ok": True,
        "present": True,
        "did": meta.get("did"),
        "purpose": meta.get("purpose"),
        "persistent": meta.get("persistent"),
        "note": "public metadata only; private key is not read",
    }


def _overlap_live_backends(state_dir: Path) -> dict[str, Any]:
    forbidden = {
        "scout": SCOUT_STATE,
        "legacy_scout": LEGACY_SCOUT_STATE,
        "bench": BENCH_STATE,
        "router": ROUTER_STATE,
        "sentinel": SENTINEL_STATE,
    }
    resolved = state_dir.expanduser().resolve(strict=False)
    overlaps = {
        name: str(path)
        for name, path in forbidden.items()
        if resolved == path.expanduser().resolve(strict=False)
    }
    return {
        "name": "no_sibling_state_overlap",
        "ok": not overlaps,
        "overlaps": overlaps,
    }
