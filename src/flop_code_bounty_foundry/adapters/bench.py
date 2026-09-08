from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from flop_work_exchange.canonical import sha256_json
from flop_work_exchange.models import BenchVerdict, Job

from flop_code_bounty_foundry.exceptions import InertUrlError, SafetyError, ValidationError
from flop_code_bounty_foundry.models import Bounty

PASSIVE_ADAPTERS = {
    "file_exists",
    "file_sha256",
    "text_contains",
    "json_path_equals",
}


def looks_like_url(value: str) -> bool:
    text = value.strip()
    if not text:
        return False
    lowered = text.lower()
    if (
        lowered.startswith("http://")
        or lowered.startswith("https://")
        or lowered.startswith("www.")
    ):
        return True
    parsed = urlparse(text)
    return parsed.scheme in {"http", "https", "git", "ssh"}


def reject_url_fetch(value: Any) -> None:
    blob = json.dumps(value, sort_keys=True)
    if "http://" in blob or "https://" in blob or "git://" in blob:
        raise InertUrlError(
            "bounty URLs are inert metadata; Foundry never fetches or clones untrusted URLs"
        )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def json_path_value(obj: Any, dotted_path: str) -> Any:
    current = obj
    for part in dotted_path.split("."):
        if isinstance(current, dict):
            current = current[part]
        elif isinstance(current, list):
            current = current[int(part)]
        else:
            raise KeyError(dotted_path)
    return current


def resolve_evidence_path(evidence_root: Path, relative: str) -> Path:
    if looks_like_url(relative):
        raise InertUrlError("Bench procedure paths must be local; URLs are never fetched")
    candidate = Path(relative)
    if candidate.is_absolute():
        resolved = candidate.expanduser().resolve(strict=False)
    else:
        resolved = (evidence_root / relative).resolve(strict=False)
    root = evidence_root.expanduser().resolve(strict=False)
    if not resolved.is_relative_to(root):
        raise SafetyError(f"Bench path escapes evidence bundle: {relative}")
    return resolved


def run_passive_step(step: dict[str, Any], evidence_root: Path) -> dict[str, Any]:
    reject_url_fetch(step)
    adapter = step.get("adapter")
    if adapter == "local_command":
        raise SafetyError(
            "local command execution requires an explicit Foundry allow_local_exec flag; "
            "specs cannot self-authorize exec"
        )
    if adapter not in PASSIVE_ADAPTERS:
        raise ValidationError(f"unknown or unsupported Bench adapter: {adapter}")
    path = resolve_evidence_path(evidence_root, str(step.get("path", "")))
    if adapter == "file_exists":
        exists = path.exists()
        return {"adapter": adapter, "path": str(path), "exists": exists, "pass": exists}
    if adapter == "file_sha256":
        expected = str(step.get("sha256") or "")
        actual = _sha256_file(path) if path.exists() else None
        return {
            "adapter": adapter,
            "path": str(path),
            "sha256": actual,
            "pass": actual == expected,
        }
    if adapter == "text_contains":
        needle = str(step.get("text", ""))
        content = path.read_text(encoding="utf-8") if path.exists() else ""
        return {
            "adapter": adapter,
            "path": str(path),
            "contains": needle in content,
            "pass": needle in content,
        }
    obj = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    if obj is None:
        return {
            "adapter": adapter,
            "path": str(path),
            "pass": False,
            "error": "missing json file",
        }
    actual = json_path_value(obj, str(step["json_path"]))
    return {
        "adapter": adapter,
        "path": str(path),
        "actual": actual,
        "expected": step.get("equals"),
        "pass": actual == step.get("equals"),
    }


def verify_bounty_bench(
    bounty: Bounty, *, allow_local_exec: bool = False
) -> BenchVerdict:
    if allow_local_exec:
        raise SafetyError("approved-local exec is not enabled in this Foundry release")
    if not bounty.evidence_bundle_path:
        return BenchVerdict(result="FAIL", evidence_id=None, notes="missing evidence bundle path")
    evidence_root = Path(bounty.evidence_bundle_path)
    if not evidence_root.is_dir():
        return BenchVerdict(
            result="FAIL",
            evidence_id=None,
            notes=f"evidence bundle is not a directory: {evidence_root}",
        )
    if bounty.spec.bench.mode != "passive":
        return BenchVerdict(
            result="FAIL",
            evidence_id=None,
            notes="only passive Bench specs are accepted; approved-local is later",
        )
    observations: list[dict[str, Any]] = []
    try:
        for step in bounty.spec.bench.procedure:
            observations.append(run_passive_step(step, evidence_root))
    except (OSError, KeyError, json.JSONDecodeError, SafetyError, ValidationError) as exc:
        return BenchVerdict(
            result="FAIL",
            evidence_id=None,
            notes=f"Bench adapter error: {exc}",
            local_exec=False,
        )
    passed = bool(observations) and all(item.get("pass") is True for item in observations)
    for artifact in bounty.spec.acceptance.required_artifacts:
        path = resolve_evidence_path(evidence_root, artifact)
        if not path.exists():
            passed = False
            observations.append(
                {"adapter": "required_artifact", "path": artifact, "pass": False}
            )
    evidence_id = "ev-" + sha256_json(
        {
            "bounty_id": bounty.bounty_id,
            "observations": observations,
            "adapter": "foundry-bench-stub",
        }
    )[:32]
    notes = (
        "passive Bench-shaped checks only; no local exec; no URL fetch; "
        "not independent Bench reputation"
        if passed
        else "one or more passive Bench checks failed: "
        + ", ".join(
            str(item.get("adapter")) for item in observations if item.get("pass") is not True
        )
    )
    return BenchVerdict(
        result="PASS" if passed else "FAIL",
        evidence_id=evidence_id,
        notes=notes,
        local_exec=False,
    )


class BountyBenchAdapter:
    """Offline Bench adapter: passive file/SHA/JSON checks against a local evidence bundle.

    Real Bench is https://github.com/greg2718/flop-bench. This stub understands the
    flop-bench.test-spec.v0.1 procedure shape so software bounties can map onto
    Bench specs. It never fetches URLs and never runs local_command.
    """

    def __init__(
        self,
        lookup: Callable[[str], Bounty | None],
        *,
        allow_local_exec: bool = False,
    ) -> None:
        self.lookup = lookup
        self.allow_local_exec = allow_local_exec

    def verify_delivery(self, job: Job) -> BenchVerdict:
        bounty = self.lookup(job.job_id)
        if bounty is None:
            return BenchVerdict(
                result="FAIL",
                evidence_id=None,
                notes="no bounty mapped to this Work Exchange job",
            )
        return verify_bounty_bench(bounty, allow_local_exec=self.allow_local_exec)
