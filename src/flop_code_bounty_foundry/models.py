from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from enum import StrEnum
from pathlib import Path
from typing import Any, Self, TypeVar

from flop_work_exchange.amounts import parse_flop_to_micro, parse_micro
from flop_work_exchange.config import OperatorRelationship
from flop_work_exchange.identity import is_valid_ed25519_did
from flop_work_exchange.models import now_iso

from flop_code_bounty_foundry.constants import BOUNTY_TYPES
from flop_code_bounty_foundry.exceptions import ValidationError

T = TypeVar("T")


class BountyType(StrEnum):
    ADD_FEATURE = "add_feature"
    REPRODUCE_BUG = "reproduce_bug"
    WRITE_TEST = "write_test"
    REVIEW_PULL_REQUEST = "review_pull_request"
    IMPROVE_DOCUMENTATION = "improve_documentation"
    BUILD_ADAPTER = "build_adapter"
    CREATE_DEPLOYMENT_CONFIGURATION = "create_deployment_configuration"
    TEST_PROTOCOL_CONFORMANCE = "test_protocol_conformance"


class BountyStatus(StrEnum):
    POSTED = "POSTED"
    ASSIGNED = "ASSIGNED"
    WORK_SUBMITTED = "WORK_SUBMITTED"
    REVIEW_REQUESTED = "REVIEW_REQUESTED"
    REVIEW_SUBMITTED = "REVIEW_SUBMITTED"
    VERIFIED = "VERIFIED"
    SETTLED = "SETTLED"
    FAILED = "FAILED"
    REJECTED = "REJECTED"


def _from_mapping(cls: type[T], raw: dict[str, Any]) -> T:
    allowed = {item.name for item in fields(cls)}  # type: ignore[arg-type]
    payload = {key: value for key, value in raw.items() if key in allowed}
    return cls(**payload)


def _require_str(raw: dict[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{key} is required")
    return value


@dataclass
class AcceptanceCriteria:
    summary: str
    items: list[str] = field(default_factory=list)
    required_artifacts: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Self:
        if not isinstance(raw, dict):
            raise ValidationError("acceptance criteria must be an object")
        items = raw.get("items") or []
        artifacts = raw.get("required_artifacts") or []
        if not isinstance(items, list) or not all(isinstance(item, str) for item in items):
            raise ValidationError("acceptance.items must be a list of strings")
        if not isinstance(artifacts, list) or not all(
            isinstance(item, str) for item in artifacts
        ):
            raise ValidationError("acceptance.required_artifacts must be a list of strings")
        return cls(
            summary=_require_str(raw, "summary"),
            items=list(items),
            required_artifacts=list(artifacts),
        )


@dataclass
class BenchVerificationPlan:
    """Bench-shaped test spec. Adapter stub — not a reimplementation of flop-bench."""

    schema_version: str = "flop-bench.test-spec.v0.1"
    claim_id: str = ""
    hypothesis: str = ""
    requested_capabilities: list[str] = field(default_factory=list)
    mode: str = "passive"
    procedure: list[dict[str, Any]] = field(default_factory=list)
    assertions: list[dict[str, Any]] = field(default_factory=list)
    failure_conditions: list[str] = field(default_factory=list)
    provenance: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Self:
        if not isinstance(raw, dict):
            raise ValidationError("bench plan must be an object")
        mode = str(raw.get("mode") or "passive")
        if mode not in {"passive", "approved-local"}:
            raise ValidationError("bench.mode must be passive or approved-local")
        procedure = raw.get("procedure") or []
        assertions = raw.get("assertions") or []
        failures = raw.get("failure_conditions") or []
        capabilities = raw.get("requested_capabilities") or []
        if not isinstance(procedure, list):
            raise ValidationError("bench.procedure must be a list")
        if not isinstance(assertions, list):
            raise ValidationError("bench.assertions must be a list")
        if not isinstance(failures, list) or not all(isinstance(item, str) for item in failures):
            raise ValidationError("bench.failure_conditions must be a list of strings")
        if not isinstance(capabilities, list) or not all(
            isinstance(item, str) for item in capabilities
        ):
            raise ValidationError("bench.requested_capabilities must be a list of strings")
        provenance = raw.get("provenance") or {}
        if not isinstance(provenance, dict):
            raise ValidationError("bench.provenance must be an object")
        return cls(
            schema_version=str(raw.get("schema_version") or "flop-bench.test-spec.v0.1"),
            claim_id=str(raw.get("claim_id") or ""),
            hypothesis=str(raw.get("hypothesis") or ""),
            requested_capabilities=list(capabilities),
            mode=mode,
            procedure=[dict(step) for step in procedure if isinstance(step, dict)],
            assertions=[dict(item) for item in assertions if isinstance(item, dict)],
            failure_conditions=list(failures),
            provenance=dict(provenance),
        )


@dataclass
class BountySpec:
    title: str
    bounty_type: str
    summary: str
    acceptance: AcceptanceCriteria
    bench: BenchVerificationPlan
    budget_micro: int
    schema: str = "flop-code-bounty-foundry.spec.v0.1"
    repo_url: str | None = None
    local_path: str | None = None
    author_did: str | None = None
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["acceptance"] = self.acceptance.to_dict()
        payload["bench"] = self.bench.to_dict()
        return payload

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Self:
        if not isinstance(raw, dict):
            raise ValidationError("bounty spec must be an object")
        bounty_type = str(raw.get("bounty_type") or "")
        if bounty_type not in BOUNTY_TYPES:
            raise ValidationError(
                f"unknown bounty_type {bounty_type!r}; expected one of {', '.join(BOUNTY_TYPES)}"
            )
        budget_micro = raw.get("budget_micro")
        if budget_micro is None:
            if raw.get("budget_flop") is None:
                raise ValidationError("budget_flop or budget_micro is required")
            budget_micro = parse_flop_to_micro(raw["budget_flop"])
        else:
            budget_micro = parse_micro(budget_micro)
        if budget_micro <= 0:
            raise ValidationError("budget must be positive")
        author_did = raw.get("author_did")
        if author_did is not None:
            if not isinstance(author_did, str) or not is_valid_ed25519_did(author_did):
                raise ValidationError("author_did must be a did:key Ed25519 DID")
        repo_url = raw.get("repo_url")
        local_path = raw.get("local_path")
        if repo_url is not None and not isinstance(repo_url, str):
            raise ValidationError("repo_url must be a string when provided")
        if local_path is not None and not isinstance(local_path, str):
            raise ValidationError("local_path must be a string when provided")
        return cls(
            schema=str(raw.get("schema") or "flop-code-bounty-foundry.spec.v0.1"),
            title=_require_str(raw, "title"),
            bounty_type=bounty_type,
            summary=_require_str(raw, "summary"),
            repo_url=repo_url,
            local_path=local_path,
            acceptance=AcceptanceCriteria.from_dict(raw.get("acceptance") or {}),
            bench=BenchVerificationPlan.from_dict(raw.get("bench") or {}),
            budget_micro=budget_micro,
            author_did=author_did,
            notes=str(raw.get("notes") or ""),
        )


@dataclass
class ReviewAssignment:
    assignment_id: str
    bounty_id: str
    reviewer_did: str
    price_micro: int
    status: str = "OPEN"
    offer_id: str | None = None
    job_id: str | None = None
    deal_id: str | None = None
    result_text: str | None = None
    result_hash: str | None = None
    verdict: str | None = None
    evidence_bundle_path: str | None = None
    operator_relationship: OperatorRelationship | None = None
    independent_reputation_eligible: bool = False
    independent_fee_volume_eligible: bool = False
    wash_risk: bool = False
    tclk_deal_id: str | None = None
    created_at: str = field(default_factory=now_iso)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Self:
        return _from_mapping(cls, raw)


@dataclass
class Bounty:
    bounty_id: str
    sponsor_did: str
    spec: BountySpec
    status: str = BountyStatus.POSTED.value
    payment_mode: str = "paper"
    created_at: str = field(default_factory=now_iso)
    implementer_did: str | None = None
    implementer_job_id: str | None = None
    implementer_offer_id: str | None = None
    implementer_deal_id: str | None = None
    implementer_price_micro: int | None = None
    implementer_relationship: OperatorRelationship | None = None
    implementer_independent_reputation_eligible: bool = False
    implementer_independent_fee_volume_eligible: bool = False
    implementer_wash_risk: bool = False
    implementer_tclk_deal_id: str | None = None
    work_artifact_hash: str | None = None
    work_result_text: str | None = None
    evidence_bundle_path: str | None = None
    review: ReviewAssignment | None = None
    bench_result: str | None = None
    bench_notes: str | None = None
    sentinel_status: str | None = None
    receipt_bundle_id: str | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["spec"] = self.spec.to_dict()
        payload["review"] = self.review.to_dict() if self.review else None
        return payload

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Self:
        spec = BountySpec.from_dict(raw.get("spec") or {})
        review_raw = raw.get("review")
        review = ReviewAssignment.from_dict(review_raw) if isinstance(review_raw, dict) else None
        payload = dict(raw)
        payload["spec"] = spec
        payload["review"] = review
        bounty = _from_mapping(cls, payload)
        bounty.notes = list(raw.get("notes") or [])
        bounty.spec = spec
        bounty.review = review
        return bounty


@dataclass
class BountyReceiptLeg:
    role: str
    receipt: dict[str, Any]
    verification: dict[str, Any]
    ledger_reason: str
    amount_micro: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Self:
        return cls(
            role=str(raw["role"]),
            receipt=dict(raw.get("receipt") or {}),
            verification=dict(raw.get("verification") or {}),
            ledger_reason=str(raw.get("ledger_reason") or ""),
            amount_micro=int(raw.get("amount_micro") or 0),
        )


@dataclass
class BountyReceiptBundle:
    bundle_id: str
    bounty_id: str
    foundry_did: str
    payment_mode: str = "paper"
    settlement_status: str = "simulated"
    settlement_execution: str = "DISABLED"
    created_at: str = field(default_factory=now_iso)
    legs: list[BountyReceiptLeg] = field(default_factory=list)
    schema: str = "flop-code-bounty-foundry.receipt-bundle.v0.1"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "bundle_id": self.bundle_id,
            "bounty_id": self.bounty_id,
            "foundry_did": self.foundry_did,
            "payment_mode": self.payment_mode,
            "settlement_status": self.settlement_status,
            "settlement_execution": self.settlement_execution,
            "created_at": self.created_at,
            "legs": [leg.to_dict() for leg in self.legs],
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Self:
        legs = [
            BountyReceiptLeg.from_dict(item)
            for item in (raw.get("legs") or [])
            if isinstance(item, dict)
        ]
        return cls(
            bundle_id=str(raw["bundle_id"]),
            bounty_id=str(raw["bounty_id"]),
            foundry_did=str(raw["foundry_did"]),
            payment_mode=str(raw.get("payment_mode") or "paper"),
            settlement_status=str(raw.get("settlement_status") or "simulated"),
            settlement_execution=str(raw.get("settlement_execution") or "DISABLED"),
            created_at=str(raw.get("created_at") or now_iso()),
            legs=legs,
            schema=str(raw.get("schema") or "flop-code-bounty-foundry.receipt-bundle.v0.1"),
        )


def did_slug(did: str) -> str:
    if not is_valid_ed25519_did(did):
        return "invalid-did"
    return did.replace(":", "_")


def load_spec_file(path: Path) -> BountySpec:
    suffix = path.suffix.lower()
    text = path.read_text(encoding="utf-8")
    if suffix == ".json":
        raw: Any = json.loads(text)
    elif suffix in {".yaml", ".yml"}:
        import yaml

        raw = yaml.safe_load(text)
    else:
        raise ValidationError(f"unsupported spec format: {suffix}")
    if not isinstance(raw, dict):
        raise ValidationError("spec file must contain an object")
    return BountySpec.from_dict(raw)
