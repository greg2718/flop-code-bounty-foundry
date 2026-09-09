from __future__ import annotations

import hashlib
import json
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from flop_work_exchange.adapters.process import CommandResult, run_argv
from flop_work_exchange.canonical import sha256_json
from flop_work_exchange.models import BenchVerdict, Job

from flop_code_bounty_foundry.exceptions import (
    AdapterError,
    InertUrlError,
    SafetyError,
    ValidationError,
)
from flop_code_bounty_foundry.models import Bounty

PASSIVE_ADAPTERS = {
    "file_exists",
    "file_sha256",
    "text_contains",
    "json_path_equals",
}

CommandRunner = Callable[..., CommandResult]
BountyLookup = Callable[[str], Bounty | None]


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

    kind = "stub"

    def __init__(
        self,
        lookup: BountyLookup | None = None,
        *,
        allow_local_exec: bool = False,
    ) -> None:
        self.lookup = lookup
        self.allow_local_exec = allow_local_exec

    def probe(self) -> dict[str, Any]:
        return {
            "ok": True,
            "kind": self.kind,
            "note": (
                "offline passive file/SHA/JSON checks; no flop-bench process; "
                "not independent Bench reputation"
            ),
            "allow_local_exec": False,
        }

    def verify_bounty(self, bounty: Bounty) -> BenchVerdict:
        return verify_bounty_bench(bounty, allow_local_exec=self.allow_local_exec)

    def verify_delivery(self, job: Job) -> BenchVerdict:
        if self.lookup is None:
            return BenchVerdict(
                result="FAIL",
                evidence_id=None,
                notes="no bounty lookup bound to this Bench adapter",
            )
        bounty = self.lookup(job.job_id)
        if bounty is None:
            return BenchVerdict(
                result="FAIL",
                evidence_id=None,
                notes="no bounty mapped to this Work Exchange job",
            )
        return self.verify_bounty(bounty)


class LocalBountyBenchAdapter:
    """Invoke ``flop-bench verify`` on the bounty's passive spec.

    Always uses an isolated temporary ``--state-dir`` (never
    ``~/.flop_agents/bench``). ``--allow-local-exec`` is omitted unless
    ``allow_local_exec=True`` (default false). Specs cannot self-authorize
    exec. Same-operator Bench results are not independent reputation.
    """

    kind = "local"

    def __init__(
        self,
        *,
        argv: Sequence[str] | None = None,
        allow_local_exec: bool = False,
        timeout_seconds: float = 60.0,
        run_command: CommandRunner | None = None,
        lookup: BountyLookup | None = None,
    ) -> None:
        self.argv = [str(part) for part in argv] if argv else None
        self.allow_local_exec = allow_local_exec
        self.timeout_seconds = timeout_seconds
        self._run_command = run_command or run_argv
        self.lookup = lookup

    def probe(self) -> dict[str, Any]:
        try:
            argv = self._resolved_argv()
        except AdapterError as exc:
            return {
                "ok": False,
                "kind": self.kind,
                "error": str(exc),
                "allow_local_exec": False,
            }
        return {
            "ok": True,
            "kind": self.kind,
            "argv": argv,
            "allow_local_exec": self.allow_local_exec,
            "note": (
                "passive flop-bench verify of bounty spec; temp --state-dir; "
                "not independent reputation"
            ),
        }

    def verify_bounty(self, bounty: Bounty) -> BenchVerdict:
        if not bounty.evidence_bundle_path:
            return BenchVerdict(
                result="FAIL", evidence_id=None, notes="missing evidence bundle path"
            )
        evidence_root = Path(bounty.evidence_bundle_path)
        if not evidence_root.is_dir():
            return BenchVerdict(
                result="FAIL",
                evidence_id=None,
                notes=f"evidence bundle is not a directory: {evidence_root}",
            )
        if bounty.spec.bench.mode != "passive" and not self.allow_local_exec:
            return BenchVerdict(
                result="FAIL",
                evidence_id=None,
                notes="only passive Bench specs are accepted unless allow_local_exec is set",
            )
        argv_prefix = self._resolved_argv()
        try:
            spec = bounty_bench_spec(bounty, allow_local_exec=self.allow_local_exec)
        except (SafetyError, ValidationError, InertUrlError) as exc:
            return BenchVerdict(
                result="FAIL",
                evidence_id=None,
                notes=f"Bench adapter error: {exc}",
                local_exec=False,
            )
        with tempfile.TemporaryDirectory(prefix="flop-cbf-bench-") as raw_tmp:
            tmp = Path(raw_tmp)
            spec_path = tmp / "spec.json"
            bench_state = tmp / "bench-state"
            spec_path.write_text(
                json.dumps(spec, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            command = [
                *argv_prefix,
                "verify",
                str(spec_path),
                "--state-dir",
                str(bench_state),
            ]
            if self.allow_local_exec:
                command.append("--allow-local-exec")
            result = self._run_command(command, timeout_seconds=self.timeout_seconds)
            return _verdict_from_bench_output(result)

    def verify_delivery(self, job: Job) -> BenchVerdict:
        if self.lookup is None:
            return BenchVerdict(
                result="FAIL",
                evidence_id=None,
                notes="no bounty lookup bound to this Bench adapter",
            )
        bounty = self.lookup(job.job_id)
        if bounty is None:
            return BenchVerdict(
                result="FAIL",
                evidence_id=None,
                notes="no bounty mapped to this Work Exchange job",
            )
        return self.verify_bounty(bounty)

    def _resolved_argv(self) -> list[str]:
        if self.argv:
            return list(self.argv)
        raise AdapterError(
            "LocalBountyBenchAdapter: flop-bench CLI missing. Set FLOP_CBF_BENCH_CLI or "
            "FLOP_CBF_BENCH_REPO (expected `flop-bench verify --state-dir ...`)."
        )


def bounty_bench_spec(bounty: Bounty, *, allow_local_exec: bool = False) -> dict[str, Any]:
    """flop-bench.test-spec.v0.1 payload with evidence paths resolved locally."""
    if not bounty.evidence_bundle_path:
        raise ValidationError("missing evidence bundle path")
    evidence_root = Path(bounty.evidence_bundle_path)
    procedure: list[dict[str, Any]] = []
    for step in bounty.spec.bench.procedure:
        reject_url_fetch(step)
        adapter = step.get("adapter")
        if adapter == "local_command" and not allow_local_exec:
            raise SafetyError(
                "local command execution requires an explicit Foundry allow_local_exec flag; "
                "specs cannot self-authorize exec"
            )
        if adapter not in PASSIVE_ADAPTERS and not (
            adapter == "local_command" and allow_local_exec
        ):
            raise ValidationError(f"unknown or unsupported Bench adapter: {adapter}")
        rewritten = dict(step)
        if "path" in rewritten:
            rewritten["path"] = str(resolve_evidence_path(evidence_root, str(rewritten["path"])))
        procedure.append(rewritten)
    plan = bounty.spec.bench.to_dict()
    plan["procedure"] = procedure
    if not allow_local_exec:
        plan["mode"] = "passive"
    plan.setdefault("schema_version", "flop-bench.test-spec.v0.1")
    plan.setdefault("claim_id", bounty.bounty_id)
    provenance = dict(plan.get("provenance") or {})
    provenance.setdefault("source", "flop-code-bounty-foundry")
    provenance["bounty_id"] = bounty.bounty_id
    provenance["note"] = (
        "same-operator Bench verification is not independent peer reputation "
        "or independent fee volume"
    )
    plan["provenance"] = provenance
    return plan


def _verdict_from_bench_output(result: CommandResult) -> BenchVerdict:
    payload = _parse_json_object(result.stdout) if result.stdout.strip() else None
    if payload is None:
        raise AdapterError(
            "flop-bench verify did not return JSON "
            f"(exit {result.returncode}): {_brief(result.stderr or result.stdout)}"
        )
    raw_result = str(payload.get("result") or "")
    if raw_result not in {"PASS", "FAIL", "PARTIAL"}:
        raise AdapterError(f"flop-bench verify returned unknown result: {raw_result!r}")
    evidence_id = payload.get("evidence_id")
    safety = payload.get("safety_report") if isinstance(payload.get("safety_report"), dict) else {}
    local_exec = bool(safety.get("local_execution")) if isinstance(safety, dict) else False
    notes = (
        "flop-bench verify; not independent Bench reputation; "
        f"local_exec={local_exec}; allow_local_exec_flag="
        f"{'--allow-local-exec' in result.argv}"
    )
    if result.returncode != 0 and raw_result == "PASS":
        raise AdapterError(
            f"flop-bench verify exit {result.returncode} but JSON result=PASS; failing closed"
        )
    return BenchVerdict(
        result=raw_result,  # type: ignore[arg-type]
        evidence_id=str(evidence_id) if evidence_id else None,
        notes=notes,
        local_exec=local_exec,
    )


def _parse_json_object(text: str) -> dict[str, Any] | None:
    try:
        loaded = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            loaded = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    return loaded if isinstance(loaded, dict) else None


def _brief(text: str, limit: int = 240) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[:limit] + "…"
