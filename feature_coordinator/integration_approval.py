"""W4.7 (M2): the operator authorization of an integration job (design §3 `authorization`, §6.2, §8.1 `approve`).

    python -m feature_coordinator integration approve --job J --stage inspect --record FILE [--signature FILE]
        [--apply] [--json]

The authorization record FILE is a JSON object, signed by the operator with their SSH key:

    {"kind": "w47-integration-approval", "version": 1, "job": "int-20261112-0001", "stage": "inspect",
     "operator": "<github login>", "issued_at": "2026-11-12T09:00:00Z", "expires_at": "2026-11-12T21:00:00Z",
     "source": {"repo": "owner/name", "ref": "<branch>", "sha": "<40 hex>"}, "base": {"ref": "dev", "sha": "<40 hex>"},
     "issue": 63, "pr": null, "paths": ["<exact repository path>", ...], "accept_unverified_provenance": false,
     "note": "optional, one line"}

    ssh-keygen -Y sign -f ~/.ssh/<key> -n mx-admin-cloud-integration-approval@feature-coordinator FILE   # → FILE.sig

What makes it an authorization (every check fails closed; nothing is inferred from GitHub — no comment, label, review
or PR state is ever read as an approval):
  * Identity: the signature must verify (`ssh-keygen -Y verify`) for the principal = the record's `operator`, in the
    integration namespace (a Git commit or other SSH signature cannot be replayed as an approval), against the
    operator-maintained `github.integration.allowed_signers` file — a regular file outside the repository checkout,
    owned by the operator and writable by nobody else; and `operator` must be in `github.integration.operators`.
  * Binding: `job`, `source` (repo, ref, full SHA), `base` (ref, full SHA), `issue` and `pr` must equal the job's
    pinned values exactly (an identical request under another job ID needs its own approval).
  * Time: `issued_at` not in the future (5 minutes clock skew), `expires_at` in the future, and at most
    MAX_LIFETIME after `issued_at`.
  * Scope: `stage` is `inspect` (the merge stage is M5); `paths` are exact repository paths (no globs) authorized one
    by one for the approval-required / unowned / symlink / git-control rules of the inspection (a protected path can
    never be authorized: the record is refused); `accept_unverified_provenance` is the operator's explicit acceptance
    of commits whose identity GitHub cannot verify (it never turns them into verified provenance).

Recording (--apply): the exact signed text, its SHA-256, the signature and the verified key fingerprint are appended
to the job's `approvals` (append-only; ids apr-0001, …) with an `integration-approval` event. A refused attempt is
logged as an `integration-approval-refused` event and changes nothing else. The job must be in AWAITING_APPROVAL or
INSPECTING.

Ordering and replacement. A new record becomes the effective approval only when its `issued_at` is STRICTLY later
(full timestamp precision, parse_record_time) than that of every approval of the stage already recorded: an older
record — typically a broader one signed earlier and never recorded — is refused, and so is a distinct record issued at
the same instant (the order of the two decisions would be ambiguous; sign a new record). Repeating a recorded record
byte for byte is idempotent (`existing`, nothing changes) and never makes an older, superseded approval effective
again; the result says whether the record is the effective one. The ordering check, the duplicate check and a fresh
time-window check are repeated inside the coordinator transaction that appends the entry, so concurrent `approve`
runs are serialized and the newest-issued record always ends up effective whatever order they finish in.

Validity is checked again whenever the approval is USED (effective_approval_problems, and time_problems with a fresh
clock reading inside the transaction that records an inspection decision): the newest inspect approval is the
effective one; once it has expired, its key has been removed from the allowed-signers file or its operator from
`operators`, it authorizes nothing (no fallback to an older approval) and a new record is needed.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import stat
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from .config import ProjectConfig
from .integration import (
    APPROVAL_NAMESPACE,
    MAX_RECORD_BYTES,
    MAX_SIGNATURE_BYTES,
    SIGNATURE_HEADER,
    IntegrationError,
    IntegrationJob,
    IntegrationJobs,
    JobState,
    approval_binding_problems,
    clean_text,
    effective_approval,
    job_summary,
    parse_approval_record,
    parse_record_time,
)
from .integration_paths import check_authorizable
from .model import utc_now
from .paths import PathPolicy

MAX_LIFETIME = timedelta(days=7)
CLOCK_SKEW = timedelta(minutes=5)
MAX_ALLOWED_SIGNERS = 1 << 20
APPROVABLE_STATES = frozenset({JobState.AWAITING_APPROVAL, JobState.INSPECTING})
_GOOD_RE = re.compile(r'Good "(?P<ns>[^"]+)" signature for (?P<principal>\S+) with \S+ key '
                      r'(?P<key>SHA256:[A-Za-z0-9+/]{43})')


class ApprovalRefused(IntegrationError):
    """An authorization record that is invalid, expired, mismatched or not verifiably signed by an operator."""


def now_utc() -> datetime:
    """The current time (injectable clock for the tests)."""
    return datetime.now(timezone.utc)


def allowed_signers_problem(path_text: str, repo_root: Path | None) -> str | None:  # pylint: disable=too-many-return-statements
    """Why the allowed-signers file cannot be trusted (None: it can)."""
    if not path_text:
        return "github.integration.allowed_signers is not configured: no operator key can be verified"
    path = Path(path_text)
    try:
        info = os.lstat(path)
    except OSError as exc:
        return f"the allowed-signers file {path} cannot be read ({exc.strerror or exc})"
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        return f"the allowed-signers file {path} must be a regular file (not a symbolic link)"
    if info.st_uid != os.getuid():
        return f"the allowed-signers file {path} is not owned by the operator running the coordinator"
    if info.st_mode & 0o022:
        return f"the allowed-signers file {path} is writable by group or others"
    if os.stat(path.parent).st_mode & 0o022:
        return f"the directory of the allowed-signers file {path} is writable by group or others"
    if info.st_size > MAX_ALLOWED_SIGNERS:
        return f"the allowed-signers file {path} is larger than {MAX_ALLOWED_SIGNERS} bytes"
    if repo_root is not None:
        real, root = os.path.realpath(path), os.path.realpath(repo_root)
        if real == root or real.startswith(root.rstrip("/") + "/"):
            return (f"the allowed-signers file {path} is inside the repository checkout, which an agent or a source "
                    "branch could change; keep it outside every checkout")
    return None


class SshSignatureVerifier:  # pylint: disable=too-few-public-methods
    """`ssh-keygen -Y verify` of an authorization record against the trusted allowed-signers file."""

    def __init__(self, allowed_signers: str, *, repo_root: Path | None, scratch: Path | None = None,
                 executable: str = "ssh-keygen", timeout: float = 30.0) -> None:
        self.allowed_signers = allowed_signers
        self.repo_root = repo_root
        self.scratch = scratch
        self.executable = executable
        self.timeout = timeout
        self.last_signers_digest = ""

    def signers(self) -> bytes:
        """The content of the trusted allowed-signers file now (ApprovalRefused when it cannot be trusted)."""
        problem = allowed_signers_problem(self.allowed_signers, self.repo_root)
        if problem:
            raise ApprovalRefused(problem)
        try:
            handle = os.open(self.allowed_signers, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(handle, "rb") as source:
                content = source.read(MAX_ALLOWED_SIGNERS + 1)
        except OSError as exc:
            raise ApprovalRefused(f"the allowed-signers file {self.allowed_signers} cannot be read "
                                  f"({exc.strerror or exc})") from exc
        if len(content) > MAX_ALLOWED_SIGNERS:
            raise ApprovalRefused(f"the allowed-signers file is larger than {MAX_ALLOWED_SIGNERS} bytes")
        return content

    def signers_digest(self) -> str:
        """SHA-256 of the trusted allowed-signers file now: an inspection decision compares it with the content its
        signature check used (last_signers_digest), so a key revoked meanwhile is noticed without a subprocess."""
        return hashlib.sha256(self.signers()).hexdigest()

    def verify(self, record: bytes, signature: str, principal: str) -> str:
        """The verified key's fingerprint (SHA256:…), or ApprovalRefused. ssh-keygen verifies against a private copy
        of the allowed-signers content read here (the file cannot change between the trust check and the
        verification); its SHA-256 is kept as last_signers_digest."""
        content = self.signers()
        if len(signature) > MAX_SIGNATURE_BYTES or not signature.startswith(SIGNATURE_HEADER):
            raise ApprovalRefused("the signature is not an SSH signature (ssh-keygen -Y sign)")
        base = self.scratch if self.scratch is not None and self.scratch.is_dir() else None
        workdir = tempfile.mkdtemp(prefix="integration-approval-", dir=base)
        sig_path, signers_path = Path(workdir) / "record.sig", Path(workdir) / "allowed_signers"
        try:
            sig_path.write_text(signature, encoding="ascii")
            signers_path.write_bytes(content)
            proc = subprocess.run([self.executable, "-Y", "verify", "-f", str(signers_path), "-I", principal, "-n",
                                   APPROVAL_NAMESPACE, "-s", str(sig_path)], input=record, capture_output=True,
                                  timeout=self.timeout, check=False,
                                  env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LC_ALL": "C"})
        except (OSError, subprocess.SubprocessError, UnicodeEncodeError) as exc:
            raise ApprovalRefused(f"the signature could not be verified: {clean_text(exc, 200)}") from exc
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
        output = proc.stdout.decode("utf-8", "replace") + proc.stderr.decode("utf-8", "replace")
        match = _GOOD_RE.search(output)
        if proc.returncode != 0 or match is None or match.group("ns") != APPROVAL_NAMESPACE \
                or match.group("principal") != principal:
            raise ApprovalRefused(f"the signature does not verify for operator {principal} in the integration "
                                  f"namespace with the allowed-signers file: {clean_text(output, 240)}")
        self.last_signers_digest = hashlib.sha256(content).hexdigest()
        return match.group("key")


def read_record_files(record_path: Path, signature_path: Path | None) -> tuple[str, str]:
    """(record text, signature text); ApprovalRefused when either cannot be read within its limits."""
    signature_path = signature_path or record_path.with_name(record_path.name + ".sig")
    try:
        raw = record_path.read_bytes()
        sig = signature_path.read_bytes()
    except OSError as exc:
        raise ApprovalRefused(f"cannot read the authorization record or its signature ({exc.filename}: "
                              f"{exc.strerror})") from exc
    if len(raw) > MAX_RECORD_BYTES or len(sig) > MAX_SIGNATURE_BYTES:
        raise ApprovalRefused("the authorization record or its signature is too large")
    try:
        return raw.decode("utf-8"), sig.decode("ascii")
    except UnicodeDecodeError as exc:
        raise ApprovalRefused("the record must be UTF-8 and the signature ASCII") from exc


def record_problems(job: IntegrationJob, record: dict[str, Any], *, stage: str, config: ProjectConfig,
                    now: datetime) -> list[str]:
    """Why a parsed record does not authorize *stage* of *job* now (empty: it does, signature aside)."""
    problems = approval_binding_problems(record, job)
    if record["stage"] != stage:
        problems.append(f"it authorizes the {record['stage']} stage, not {stage}")
    operators = {login.lower() for login in config.github.integration.operators}
    if not operators:
        problems.append("github.integration.operators is empty: no one may approve")
    elif record["operator"].lower() not in operators:
        problems.append(f"{record['operator']} is not in github.integration.operators")
    return problems + time_problems(record, now)


def time_problems(record: Mapping[str, Any], now: datetime) -> list[str]:
    """Why the time window of a record (or of an approval entry: the same `issued_at`/`expires_at`) does not hold at
    *now* (empty: it does). Cheap and subprocess-free, so it runs again under the coordinator lock with a FRESH clock
    reading at each decision point (an approval that expires while a slow check runs authorizes nothing)."""
    problems = []
    issued = parse_record_time(record["issued_at"], "issued_at")
    expires = parse_record_time(record["expires_at"], "expires_at")
    if issued > now + CLOCK_SKEW:
        problems.append(f"it is issued in the future ({record['issued_at']})")
    if expires <= now:
        problems.append(f"it expired at {record['expires_at']}")
    if expires - issued > MAX_LIFETIME:
        problems.append(f"its lifetime exceeds {MAX_LIFETIME.days} days")
    return problems


def verify_record(job: IntegrationJob, text: str, signature: str, *, stage: str, config: ProjectConfig,
                  verifier: SshSignatureVerifier, policy: PathPolicy, now: datetime) -> tuple[dict[str, Any], str]:
    """(parsed record, verified key fingerprint) or ApprovalRefused listing every problem."""
    try:
        record = parse_approval_record(text)
        check_authorizable(policy, config, record["paths"])
    except ApprovalRefused:
        raise
    except IntegrationError as exc:
        raise ApprovalRefused(str(exc)) from exc
    problems = record_problems(job, record, stage=stage, config=config, now=now)
    if problems:
        raise ApprovalRefused("; ".join(problems))
    fingerprint = verifier.verify(text.encode("utf-8"), signature, record["operator"])
    return record, fingerprint


def effective_approval_problems(job: IntegrationJob, *, config: ProjectConfig, verifier: SshSignatureVerifier,
                                now: datetime, stage: str = "inspect") -> tuple[dict[str, Any] | None, list[str]]:
    """(the effective approval, why it does not authorize *stage* now). The newest approval of the stage is the
    effective one (see the module docstring); its signature, operator and time window are checked again now."""
    entry = effective_approval(job, stage)
    if entry is None:
        return None, [f"{job.job_id} has no {stage} approval: an operator must sign an authorization record and run "
                      f"`integration approve --job {job.job_id} --stage {stage} --record FILE --apply`"]
    record = parse_approval_record(entry["record"])
    problems = record_problems(job, record, stage=stage, config=config, now=now)
    if not problems:
        try:
            if verifier.verify(entry["record"].encode("utf-8"), entry["signature"], record["operator"]) \
                    != entry["signer"]["key"]:
                problems.append("it was signed with another key than the one recorded")
        except ApprovalRefused as exc:
            problems.append(str(exc))
    return entry, [f"approval {entry['id']}: {problem}" for problem in problems]


def approve(jobs: IntegrationJobs, config: ProjectConfig, job_id: str, *, stage: str, record_path: Path,
            signature_path: Path | None, verifier: SshSignatureVerifier, policy: PathPolicy, apply: bool,
            clock: Callable[[], datetime] = now_utc) -> dict[str, Any]:
    """Verify an authorization record for *job_id* and (with *apply*) append it to the job's approvals. Returns
    {outcome, changed, reasons, approval, effective, job} with outcome would-record | recorded | existing | refused;
    `effective` says whether the (new or replayed) record is, or would be, the job's effective approval."""
    job = jobs.load(job_id)
    reasons: list[str] = []
    record_sha = ""
    try:
        if job.state not in APPROVABLE_STATES:
            raise ApprovalRefused(f"{job_id} is {job.state.value}: only a job in AWAITING_APPROVAL or INSPECTING takes "
                                  "an inspect approval" + (" (terminal jobs are never reopened)" if job.terminal
                                                           else ""))
        text, signature = read_record_files(record_path, signature_path)
        record_sha = _sha256(text)
        record, fingerprint = verify_record(job, text, signature, stage=stage, config=config, verifier=verifier,
                                            policy=policy, now=clock())
    except ApprovalRefused as exc:
        reasons = [str(exc)]
    if reasons:
        if apply:
            with jobs.store.transaction():
                jobs.store.append_event("integration-approval-refused", job=job_id, stage=stage,
                                        record_sha256=record_sha, reason=clean_text(reasons[0]))
        return {"outcome": "refused", "changed": False, "reasons": reasons, "approval": None, "effective": False,
                "job": job_summary(job)}
    settled = _settled(job, record, record_sha)
    if settled is not None:
        if apply and settled["outcome"] == "refused":
            with jobs.store.transaction():
                jobs.store.append_event("integration-approval-refused", job=job_id, stage=stage,
                                        record_sha256=record_sha, reason=clean_text(settled["reasons"][0]))
        return settled
    if not apply:
        preview = _entry(record, text, signature, fingerprint, f"apr-{len(job.approvals) + 1:04d}")
        return {"outcome": "would-record", "changed": False, "reasons": [], "approval": approval_view(preview),
                "effective": True, "job": job_summary(job)}
    with jobs.store.transaction():
        # Atomic replacement: the job is re-read under the coordinator lock and the duplicate, ordering and time
        # checks are repeated there, so a concurrent approve (or a slow verification) cannot interleave.
        job = jobs.load(job_id)
        late = [] if job.state in APPROVABLE_STATES else [f"{job_id} became {job.state.value} meanwhile"]
        settled = None if late else _settled(job, record, record_sha)
        if settled is not None and settled["outcome"] == "existing":
            return settled
        late += settled["reasons"] if settled is not None else time_problems(record, clock())
        if late:
            jobs.store.append_event("integration-approval-refused", job=job_id, stage=stage,
                                    record_sha256=record_sha, reason=clean_text(late[0]))
            return {"outcome": "refused", "changed": False, "approval": None, "effective": False,
                    "job": job_summary(job), "reasons": [f"{reason}; nothing was recorded" for reason in late]}
        entry = _entry(record, text, signature, fingerprint, f"apr-{len(job.approvals) + 1:04d}")
        job.approvals.append(entry)
        jobs.save(job)
        jobs.store.append_event("integration-approval", job=job_id, approval=entry["id"], stage=stage,
                                operator=entry["operator"], key=fingerprint, record_sha256=record_sha,
                                expires_at=entry["expires_at"], paths=len(entry["paths"]),
                                accept_unverified_provenance=entry["accept_unverified_provenance"])
    return {"outcome": "recorded", "changed": True, "reasons": [], "approval": approval_view(entry),
            "effective": True, "job": job_summary(job)}


def _settled(job: IntegrationJob, record: dict[str, Any], record_sha: str) -> dict[str, Any] | None:
    """The answer when a verified record needs no new entry: `existing` (recorded already, byte for byte — it stays
    what it was: the effective approval, or a superseded one that authorizes nothing) or `refused` (not strictly
    newer than every recorded approval of its stage); None: it may be appended and becomes the effective one."""
    existing = next((entry for entry in job.approvals if entry["record_sha256"] == record_sha), None)
    if existing is not None:
        current = effective_approval(job, existing["stage"])
        effective = current is not None and current["id"] == existing["id"]
        reasons = [] if effective else [
            f"the record is already recorded as {existing['id']}, which the newer approval "
            f"{current['id'] if current else '?'} supersedes: replaying it changes nothing and it authorizes nothing"]
        return {"outcome": "existing", "changed": False, "reasons": reasons, "approval": approval_view(existing),
                "effective": effective, "job": job_summary(job)}
    problem = ordering_problem(job, record)
    if problem:
        return {"outcome": "refused", "changed": False, "reasons": [problem], "approval": None, "effective": False,
                "job": job_summary(job)}
    return None


def ordering_problem(job: IntegrationJob, record: Mapping[str, Any]) -> str:
    """Why a NEW record may not become the effective approval of its stage (empty: it may): it must be issued
    STRICTLY after every approval of the stage already recorded, compared at full precision. An older record (e.g. a
    broader one signed earlier, never recorded) must not undo a later, narrower decision; a distinct record issued at
    the same instant is refused because the order of the two decisions is ambiguous."""
    issued = parse_record_time(record["issued_at"], "issued_at")
    for entry in job.approvals:
        if entry["stage"] != record["stage"]:
            continue
        recorded = parse_record_time(entry["issued_at"], "issued_at")
        if issued < recorded:
            return (f"it was issued at {record['issued_at']}, before the recorded approval {entry['id']} "
                    f"({entry['issued_at']}): a newer decision is never replaced by an older record")
        if issued == recorded:
            return (f"it was issued at the same instant as the recorded approval {entry['id']} ({entry['issued_at']}) "
                    "but is a different record: the order of the two decisions is ambiguous; sign a new record with a "
                    "later issued_at")
    return ""


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _entry(record: dict[str, Any], text: str, signature: str, fingerprint: str, approval_id: str) -> dict[str, Any]:
    return {"id": approval_id, "stage": record["stage"], "operator": record["operator"],
            "issued_at": record["issued_at"], "expires_at": record["expires_at"], "source_sha": record["source"]["sha"],
            "base_sha": record["base"]["sha"], "issue": record["issue"], "pr": record["pr"], "paths": record["paths"],
            "accept_unverified_provenance": record["accept_unverified_provenance"], "note": record.get("note", ""),
            "record": text, "record_sha256": _sha256(text), "signature": signature,
            "signer": {"method": "ssh-sig", "namespace": APPROVAL_NAMESPACE, "principal": record["operator"],
                       "key": fingerprint},
            "recorded_at": utc_now(), "actor": record["operator"]}


def approval_view(entry: dict[str, Any]) -> dict[str, Any]:
    """An approval without its record text and signature (status output)."""
    return {k: v for k, v in entry.items() if k not in ("record", "signature")}


def check_stage(value: str) -> str:
    """`inspect` (M2); `merge` is M5."""
    if value != "inspect":
        raise IntegrationError(f"--stage {value!r}: only the inspect stage exists in M2 (the merge stage is M5)")
    return value
