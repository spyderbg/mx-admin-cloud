"""W4.7 (M5; Codex F1, F2, F3 and F7, and the routing / F7a / F7b re-review): the WRITE BOUNDARY of an integration
job's two GitHub writes.

A GitHub write of an integration job (open the PR, merge the PR) is authorized by nothing its caller says or supplies:
not a job ID, a PR number, an "authorized" flag, a digest — and not an authorization object either. The GitHub client
(github_api.GitHubClient) builds THIS concrete authority itself, for every write, from the TRUST ANCHORS bound to it
once (`IntegrationAnchors`): the coordinator state directory whose persisted jobs authorize writes, the checkout, and
the project configuration's authoritative source (the file `integration merge` was given, read again at every write).
Only the `integration merge` command binds them, while it builds its client and before the workflow gets it, through the
client's PRIVATE `_bind_integration`: the client's API offers no way to bind, replace or supply anchors, an authority, a
verifier, a clock or a policy, and a client without anchors sends neither write.

One write is authorized under ONE policy snapshot, as close to the request as practical:

  1. the configuration is read from its source — a snapshot whose fingerprint (`configuration_fingerprint`: the whole
     effective configuration, canonical) identifies it — and must enable integration for the client's repository;
     a source that cannot be read or parsed is a refusal, never a fallback to an earlier, more permissive copy;
  2. the job is read from the anchored state directory (an unknown job is a refusal) and must be
     AWAITING_MERGE_APPROVAL with its candidate;
  3. under the snapshot: the effective MERGE approval is verified again, signature included (operator, key,
     namespace, time window at a fresh clock reading, binding to this candidate, validation and review evidence);
     the INSPECT approval the candidate was built under is verified again too — signature, key and operator (its
     EXPIRY does not matter: the inspection it authorized is finished data; a revoked key does); both against the
     same allowed-signers content;
  4. the persisted write-ahead intent — the newest `merge-attempt`, open and never dispatched — names exactly this
     operation, PR, SHA, head, base and repository (integration_merge_schema), and the request goes to exactly the
     ROUTE that operation has (github_api.integration_route: the snapshot's repository and, for a merge, the intent's
     PR — the client's mutable `repo_path` plays no part; the client sends to the route it was authorized for and
     nothing else);
  5. the candidate's PATHS are classified again under the snapshot (integration_paths.classify_paths — the M2/M3
     classifier, not a second one): the candidate's change re-derived from hash-verified objects of the job's
     candidate repository (the candidate commit must be the recorded head, tree and parents, and its changed paths
     the recorded ones; symlink targets included), and the source history's change re-derived from hash-verified
     objects of the checkout by M2's own derivation (integration_inspect.history_changes; it must be M2's recorded
     path list — which carries no symlink targets, so it is never classified on its own): a path newly protected, a
     symlink — even one only the history contains — whose target is newly protected, a path newly approval-required,
     unowned (a role removed) or otherwise in need of an explicit authorization the inspect approval does not give
     refuses;
  6. M3's validation and M4's review are current for the gate plan the snapshot derives from the candidate's paths AS
     CLASSIFIED in 5 (the current owning roles, never the roles recorded under an older configuration), and the
     merge approval's bound validation names that plan: an obsolete plan refuses (nothing is run or replaced);
  7. for a PR creation: no OTHER job's history for the same source branch says a PR may exist that GitHub does not
     show (a dispatched creation with an unknown outcome — after an abort, a supersession, a restart —, or a recorded
     PR): integration_merge_schema.creation_history_problems;
  8. for a merge: every check the snapshot requires (`required_checks`) is observed again on GitHub, now, for the
     approved head, and passed (integration_merge_github: a missing, pending, failed, unreadable or ambiguous check
     refuses; an empty list claims no CI result). Checks are not a condition of opening the PR: CI usually runs on
     the PR;
  9. in ONE coordinator transaction (the store-wide lock): the job state, the effective approval and its window at a
     fresh clock reading, the intent still open, undispatched and naming exactly this request and route (4), the
     cross-job creation history (7) again, the allowed-signers content unchanged since the signatures were verified,
     and the configuration read AGAIN with the SAME fingerprint as the snapshot (changed or unreadable: refused — a
     fresh run decides under the new policy) — then `merge-dispatch` evidence is appended, carrying the snapshot's
     fingerprint (`policy_sha256`). The entry is
     the one-shot of the write (a second request for the attempt is refused, across processes and restarts), and
     what recovery relies on: an attempt WITHOUT it was never sent.

Any problem raises IntegrationWriteRefused before anything is sent; whatever breaks inside the boundary is a refusal.

Unavoidable races, stated: GitHub is not part of the coordinator's transaction. A configuration or allowed-signers
change after the final re-read (inside the transaction, immediately before the dispatch record) applies from the next
write; a check that flips after it was read, or a branch that moves, is GitHub's to enforce (the merge request pins
the head SHA; GitHub's own branch protection is the only atomic guard).

Residual boundary, stated: this protects the supported API — a holder of a GitHub client, or any caller of its public
methods, cannot authorize a write, choose the anchors, or reach the transport — and the workflow against its own stale
view of the policy. It does not protect against arbitrary code running inside the coordinator process (code that calls
private members, replaces the client, this module or the transport, or runs `gh api` with the operator's login), and the
anchors are as trustworthy as the command line that names them: `integration merge --state-dir X --project-config Y`
with another directory or file is operating another coordinator. An exception after the dispatch entry was saved (a
failing audit-event append) leaves the attempt dispatched although nothing was sent: unresolved, never duplicated; the
operator aborts it. """

from __future__ import annotations

import dataclasses
import functools
import hashlib
import json
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any, Callable, Mapping

from .config import ProjectConfig, load_config
from .errors import CoordinatorError, GitHubError, IntegrationWriteRefused
from .github_api import integration_route
from .integration import (
    IntegrationJob,
    IntegrationJobs,
    JobState,
    effective_approval,
    evidence_digest,
    latest_evidence,
    parse_record_time,
)
from .integration_approval import (
    ApprovalRefused,
    SshSignatureVerifier,
    effective_approval_problems,
    now_utc,
    time_problems,
)
from .integration_candidate import CandidateArea, CandidateGit
from .integration_gates import gate_plan, plan_view
from .integration_inspect import history_changes
from .integration_intake import ReadOnlyGit
from .integration_merge_github import check_observations, evaluate_checks
from .integration_merge_schema import attempt_dispatch, creation_history_problems, merge_pr_entry, open_merge_attempt
from .integration_paths import ClassifiedPaths, PathChange, candidate_changes, classify_paths
from .integration_review import review_eligibility
from .integration_validate import validated_for
from .locks import LockSet
from .model import utc_now
from .paths import PathPolicy
from .store import StateStore

OPERATIONS = ("create-pr", "merge")


@dataclass(frozen=True)
class _Write:
    """What the caller is about to send: the operation, its recorded attempt, the repository, the request facts and
    the route (method, path) the request goes to."""

    operation: str
    attempt: str
    repository: str
    request: Mapping[str, Any]
    route: tuple[str, ...]


@dataclass(frozen=True)
class IntegrationAnchors:
    """The trust anchors of one GitHub client's integration writes (module docstring), bound once and never replaced:
    the coordinator state directory, the checkout, and `configuration` — the project configuration read from its
    authoritative source NOW (it raises when the source cannot be read or is invalid)."""

    state_dir: Path
    repo: Path
    configuration: Callable[[], ProjectConfig]

    @classmethod
    def from_file(cls, state_dir: Path, repo: Path, config_file: Path) -> "IntegrationAnchors":
        """The anchors of `integration merge`: its --state-dir, --repo and --project-config FILE."""
        return cls(Path(state_dir), Path(repo), functools.partial(load_config, Path(config_file)))


def _canonical(value: Any) -> Any:
    if isinstance(value, (set, frozenset)):
        return sorted(str(item) for item in value)
    return str(value)


def configuration_fingerprint(config: ProjectConfig) -> str:
    """SHA-256 of the WHOLE effective project configuration, canonical: the identity of the policy snapshot a write is
    decided under. Any change — the integration switch, operators, the allowed-signers path, the repository, path
    patterns, roles, required checks, gates, the review policy — gives another fingerprint."""
    text = json.dumps(dataclasses.asdict(config), sort_keys=True, separators=(",", ":"), default=_canonical)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def inspect_still_verifies(job: IntegrationJob, inspect: Mapping[str, Any], config: ProjectConfig,
                           verifier: SshSignatureVerifier) -> list[str]:
    """Why the inspect approval the candidate was built under no longer verifies (empty: it does). It authorized the
    candidate's paths and accepted its provenance, so it is used as recorded data: its EXPIRY does not matter at merge
    time — but its signature, key and operator must still verify against the CURRENT policy (a key removed from the
    allowed-signers file revokes every approval made with it, design §15 item 1). Its signature is checked as of its
    own time window."""
    at = parse_record_time(inspect["issued_at"], "issued_at") + timedelta(seconds=1)
    _entry, problems = effective_approval_problems(job, config=config, verifier=verifier, now=at)
    return [f"the inspect approval {inspect['id']} that authorized the candidate no longer verifies (a revoked key or "
            f"operator?): {problem}" for problem in problems]


def _classification_problems(classified: ClassifiedPaths, what: str) -> list[str]:
    return [f"{what}: {reason} (under the current configuration)" for reason in classified.refusals] + \
        [f"{what}: {path} needs an explicit per-path authorization under the current configuration, and the inspect "
         "approval does not give it" for path in classified.required]


class IntegrationWriteAuthority:  # pylint: disable=too-few-public-methods
    """The write boundary (module docstring). Built by the GitHub client for each write, from the anchors bound to it
    and the client itself (the boundary's GitHub reads); never accepted from a caller."""

    def __init__(self, anchors: IntegrationAnchors, client: Any) -> None:
        self.anchors, self.client = anchors, client
        self.store = StateStore(Path(anchors.state_dir))
        self.jobs = IntegrationJobs(self.store)

    # -- the boundary ----------------------------------------------------------------------------------------------

    def authorize(self, operation: str, *, job_id: str, attempt: str, repository: str,  # pylint: disable=too-many-arguments
                  request: Mapping[str, Any], route: tuple[str, str]) -> None:
        """Raise IntegrationWriteRefused unless the persisted job authorizes *operation* (with *request*, the facts the
        caller is about to send, to *route*, the method and path it is about to send them to) under the current
        policy; on success the dispatch of *attempt* is recorded."""
        try:
            self._authorize(operation, job_id, attempt, repository, dict(request), tuple(route))
        except IntegrationWriteRefused:
            raise
        except Exception as exc:  # pylint: disable=broad-exception-caught  # the boundary fails CLOSED, whatever broke
            raise IntegrationWriteRefused([f"the authorization could not be established ({type(exc).__name__}: "
                                           f"{str(exc)[:200]})"]) from exc

    def _authorize(self, operation: str, job_id: str, attempt: str, repository: str,  # pylint: disable=too-many-arguments
                   request: Mapping[str, Any], route: tuple[str, ...]) -> None:
        if operation not in OPERATIONS:
            raise IntegrationWriteRefused([f"unknown integration write {operation!r}"])
        facts = [job_id, attempt, repository, *request.values(), *route]
        if any(type(value) not in (str, int, bool) for value in facts):  # pylint: disable=unidiomatic-typecheck
            raise IntegrationWriteRefused(["the facts of an integration write must be plain values"])
        config = self.anchors.configuration()
        snapshot = configuration_fingerprint(config)
        if not config.github.integration.enabled:
            raise IntegrationWriteRefused(["github.integration.enabled is false in the current configuration"])
        if repository.lower() != config.github.repository.lower():
            raise IntegrationWriteRefused([f"the client writes to {repository}, the configured repository is "
                                           f"{config.github.repository}"])
        verifier = SshSignatureVerifier(config.github.integration.allowed_signers, repo_root=Path(self.anchors.repo),
                                        scratch=Path(self.anchors.state_dir) / "tmp")
        job = self.jobs.load(job_id)
        write = _Write(operation, attempt, repository, request, route)
        approval, problems, signers = self._problems(job, config, verifier, operation, attempt, repository, request,
                                                     route)
        if problems:
            raise IntegrationWriteRefused(problems)
        assert approval is not None
        with self.store.transaction():
            current = self.jobs.load(job_id)
            again = self._unchanged(current, approval, write, config, verifier, signers, snapshot)
            if again:
                raise IntegrationWriteRefused(again)
            candidate = current.candidate
            assert candidate is not None
            entry = {"kind": "merge-dispatch", "id": f"mdi-{len(current.evidence) + 1:04d}", "at": utc_now(),
                     "candidate": candidate["evidence"], "head_sha": candidate["head_sha"],
                     "tree_sha": candidate["tree_sha"], "attempt": attempt, "action": operation,
                     "approval": approval["id"], "approval_sha256": approval["record_sha256"],
                     "policy_sha256": snapshot}
            entry["sha256"] = evidence_digest(entry)
            current.evidence.append(entry)
            self.jobs.save(current)
            self.store.append_event("integration-merge-dispatch", job=job_id, evidence=entry["id"], attempt=attempt,
                                    action=operation, approval=approval["id"], policy=snapshot[:16])

    # -- the checks under the snapshot -------------------------------------------------------------------------------

    def _problems(self, job: IntegrationJob, config: ProjectConfig, verifier: SshSignatureVerifier, operation: str,  # pylint: disable=too-many-arguments
                  attempt: str, repository: str, request: Mapping[str, Any], route: tuple[str, ...],
                  ) -> tuple[dict[str, Any] | None, list[str], str]:
        """(the effective merge approval, why the write is not authorized under *config*, the SHA-256 of the
        allowed-signers content BOTH signatures were verified against) — module docstring items 2 to 8."""
        write = _Write(operation, attempt, repository, request, tuple(route))
        if job.state is not JobState.AWAITING_MERGE_APPROVAL or job.candidate is None:
            return None, [f"{job.job_id} is {job.state.value}: an integration write needs a job awaiting its merge "
                          "approval with its candidate"], ""
        approval, problems = effective_approval_problems(job, config=config, verifier=verifier, now=now_utc(),
                                                         stage="merge")
        if problems or approval is None:
            return None, problems or ["there is no effective merge approval"], ""
        merge_signers = verifier.last_signers_digest
        inspect = effective_approval(job, "inspect")
        built = next((e for e in job.evidence if e["id"] == job.candidate["evidence"]), None)
        if inspect is None or built is None or built["approval"] != inspect["id"]:
            return None, ["the inspect approval the candidate was built under is missing or replaced"], ""
        problems = inspect_still_verifies(job, inspect, config, verifier)
        if not problems and verifier.last_signers_digest != merge_signers:
            problems = ["the allowed-signers file changed while the signatures were being verified"]
        problems += self._intent_problems(job, approval, write, config)
        entries: list[dict[str, Any]] = []
        if not problems:   # the costlier checks only for an otherwise authorized write
            entries, problems = self._path_problems(job, config, inspect)
        if not problems:
            problems = self._evidence_problems(job, config, approval, entries)
        if not problems and write.operation == "create-pr":
            problems = creation_history_problems(self.jobs.list(), job)
        if not problems and write.operation == "merge":
            problems = self._check_problems(job, config)
        return approval, problems, merge_signers

    @staticmethod
    def _evidence_problems(job: IntegrationJob, config: ProjectConfig, approval: Mapping[str, Any],
                           entries: list[dict[str, Any]]) -> list[str]:
        """M3's validation and M4's review authorize the gate plan the snapshot derives from the candidate's paths AS
        CLASSIFIED NOW (*entries*: the boundary's own classification, from verified objects — the owning roles of
        the current configuration, never the roles recorded under an older one; Codex F7b). The approval's bound
        validation must be that evidence and name that plan. Nothing is run or replaced here: an obsolete plan is a
        refusal (restore the configuration, or abort the job and supersede it)."""
        binding, plan = approval["merge"], plan_view(gate_plan(entries, config))
        validation, review = latest_evidence(job, "validation"), review_eligibility(job, config, entries)[0]
        recorded = validation["plan"] if validation is not None else None
        if recorded is not None and recorded["sha256"] != plan["sha256"]:
            before, now = {g["gate"] for g in recorded["gates"]}, {g["gate"] for g in plan["gates"]}
            roles = sorted({f"{e['path']} → {e['role'] or 'unowned'}" for e in entries})
            return [f"M3's validation {validation['id']} was decided for gate plan {recorded['sha256'][:12]}, but the "
                    f"candidate's paths classified under the current configuration ({', '.join(roles[:10])}) derive "
                    f"gate plan {plan['sha256'][:12]}: gates never run for it {sorted(now - before) or 'none'}, gates "
                    f"no longer planned {sorted(before - now) or 'none'} — the validation and the review do not "
                    "authorize the current plan (restore the configuration, or abort the job and supersede it)"]
        problems = []
        if validation is None or validation["id"] != binding["validation"]["evidence"] \
                or binding["validation"]["plan_sha256"] != plan["sha256"] or not validated_for(job, config, entries):
            problems.append("M3's validation the approval names is not current for the gate plan the current "
                            "configuration derives")
        if review is None or review["id"] != binding["review"]["evidence"]:
            problems.append("M4's review the approval names is not current and eligible under the current "
                            "configuration")
        return problems

    @staticmethod
    def _intent_problems(job: IntegrationJob, approval: Mapping[str, Any], write: _Write,
                         config: ProjectConfig) -> list[str]:
        """The recorded write-ahead intent names exactly the operation the caller is about to send, and the request
        goes to exactly the route that operation has: the snapshot's repository and the intent's PR (Codex F1)."""
        operation, attempt, repository, request = write.operation, write.attempt, write.repository, write.request
        intent = open_merge_attempt(job)
        candidate = job.candidate
        assert candidate is not None
        if intent is None or intent["id"] != attempt or intent["action"] != operation:
            return [f"no open {operation} intent {attempt!r} is recorded for {job.job_id}: the write-ahead intent must "
                    "exist (and be the newest, unended attempt) before the request"]
        if attempt_dispatch(job, intent) is not None:
            return [f"the intent {attempt} was already dispatched: an attempt authorizes one request"]
        problems = []
        if (intent["approval"], intent["approval_sha256"]) != (approval["id"], approval["record_sha256"]):
            problems.append(f"the intent {attempt} was recorded under another approval than the effective one")
        if (intent["candidate"], intent["head_sha"], intent["tree_sha"], intent["source_sha"], intent["base_sha"]) != \
                (candidate["evidence"], candidate["head_sha"], candidate["tree_sha"], job.source["sha"],
                 job.base["expected_sha"]):
            problems.append(f"the intent {attempt} is for another candidate or other pinned SHAs than the job's")
        if repository.lower() != approval["merge"]["repository"].lower():
            problems.append(f"the approval is for {approval['merge']['repository']}, not {repository}")
        if operation == "create-pr":
            wanted = {"head": job.source["ref"], "base": job.base["ref"], "draft": False}
            if dict(request) != wanted or intent["request"] != wanted:
                problems.append(f"the PR to create is {wanted}; the request names {dict(request)}")
            if approval["merge"]["authorizes"]["create_pr"] is not True or merge_pr_entry(job) is not None:
                problems.append("the signed approval does not authorize creating a PR (or one is already bound)")
        else:
            bound = merge_pr_entry(job)
            wanted_merge = {"number": intent["pr"], "merge_method": "merge", "sha": job.source["sha"]}
            if bound is None or bound["number"] != intent["pr"] or dict(request) != wanted_merge \
                    or intent["request"] != {"merge_method": "merge", "sha": job.source["sha"]} \
                    or bound["head"]["sha"] != job.source["sha"]:
                problems.append(f"the PR to merge is the bound PR #{bound['number'] if bound else '?'} at "
                                f"{job.source['sha']} with merge_method merge; the request names {dict(request)}")
        try:
            route = integration_route(operation, config.github.repository,
                                      intent["pr"] if operation == "merge" else None)
        except IntegrationWriteRefused as exc:
            return problems + exc.reasons
        if tuple(write.route) != route:
            problems.append(f"the request would go to {' '.join(map(str, write.route))[:200]}; the authorized "
                            f"{operation} goes to {route[0]} {route[1]} (the configured repository and the recorded "
                            "intent's PR) — nothing is sent elsewhere")
        return problems

    def _path_problems(self, job: IntegrationJob, config: ProjectConfig, inspect: Mapping[str, Any],
                       ) -> tuple[list[dict[str, Any]], list[str]]:
        """Module docstring item 6: (the candidate's paths classified under *config*, why a path refuses the write).
        The candidate's change is re-derived from hash-verified objects of its candidate repository; the source
        history's change — symlink targets included (Codex F7a) — from hash-verified objects of the checkout, by M2's
        own derivation (integration_inspect.history_changes); both must be the recorded ones, and both are classified
        under *config* with the inspect approval's per-path authorizations."""
        candidate = job.candidate
        assert candidate is not None
        area = CandidateArea(Path(self.anchors.state_dir), job.job_id)
        if not area.repo.is_dir():
            return [], [f"the candidate repository of {job.job_id} is missing, so its paths cannot be classified under "
                        "the current configuration (the next merge run rebuilds it)"]
        base, source = job.base["expected_sha"], job.source["sha"]
        try:
            with CandidateGit(area.home, timeout=config.github.publish.git_timeout_seconds).reader(area.repo) as reader:
                commit = reader.read(candidate["head_sha"], strict=True)
                if commit.tree != candidate["tree_sha"] or tuple(commit.parents) != (base, source):
                    return [], [f"the candidate commit {candidate['head_sha'][:12]} read from verified objects does "
                                "not have the recorded tree and the parents base_sha, source_sha"]
                changes = candidate_changes(reader, base_tree=reader.read(base, strict=False).tree,
                                            candidate_tree=candidate["tree_sha"])
        except (CoordinatorError, OSError) as exc:
            return [], [f"the candidate's paths cannot be read from verified objects: {exc}"]
        if sorted(changes) != sorted(entry["path"] for entry in candidate["paths"]):
            return [], ["the candidate's changed paths, re-derived from verified objects, are not the recorded ones"]
        history, problem = self._history(job, config)
        if history is None:
            return [], [problem]
        policy, approved = PathPolicy(Path(self.anchors.repo), config), list(inspect["paths"])
        classified = classify_paths(changes, policy, config, approved)
        return classified.entries, _classification_problems(classified, "candidate path") + \
            _classification_problems(classify_paths(history, policy, config, approved), "source history path")

    def _history(self, job: IntegrationJob, config: ProjectConfig) -> tuple[dict[str, PathChange] | None, str]:
        """(the source history's change base..source, '') re-derived from hash-verified objects of the anchored
        checkout exactly as M2 derived it — every commit of the verified range and the net diff from each proven merge
        base, with every symlink target each path was ever set to read from its blob — when it is M2's recorded path
        list (paths, kinds of change, flags); else (None, why not). The recorded list carries no symlink targets, so it
        is never classified on its own (Codex F7a)."""
        base, source = job.base["expected_sha"], job.source["sha"]
        git = ReadOnlyGit(Path(self.anchors.repo), config, LockSet(Path(self.anchors.state_dir) / "locks"),
                          timeout=config.github.publish.git_timeout_seconds)
        try:
            with git.session():
                history = history_changes(git, git.verified_range(base, source), base, source)
        except (CoordinatorError, OSError) as exc:
            return None, f"the source history's paths cannot be re-derived from verified objects: {exc}"
        derived = {path: (sorted(item.changes), sorted(item.flags)) for path, item in history.items()}
        if derived != {e["path"]: (sorted(e["changes"]), sorted(e["flags"])) for e in job.paths}:
            return None, ("the source history's changed paths, re-derived from verified objects, are not M2's recorded "
                          "ones")
        return history, ""

    def _check_problems(self, job: IntegrationJob, config: ProjectConfig) -> list[str]:
        """Module docstring item 8: the required checks of the approved head, observed on GitHub NOW."""
        required = config.github.integration.required_checks
        if not required:
            return []
        observed, unavailable = None, ""
        try:
            observed = check_observations(self.client, config.github.repository, job.source["sha"],
                                          max_pages=config.github.max_pages)
        except (GitHubError, CoordinatorError, OSError) as exc:
            unavailable = f"the checks of the approved head could not be read at the write boundary: {exc}"
        return [f"required check {item['name']} is {item['status']} at the write boundary: {item['detail']}"
                for item in evaluate_checks(required, observed, unavailable=unavailable) if item["status"] != "pass"]

    # -- inside the transaction -------------------------------------------------------------------------------------

    def _unchanged(self, job: IntegrationJob, approval: Mapping[str, Any], write: _Write, config: ProjectConfig,  # pylint: disable=too-many-arguments,too-many-return-statements
                   verifier: SshSignatureVerifier, signers: str, snapshot: str) -> list[str]:
        """Module docstring item 9: a state change, a replaced or expired approval, an intent that is no longer open and
        undispatched or no longer names exactly this request and route (another PR bound meanwhile), a new cross-job
        creation, a changed allowed-signers file or a configuration that is no longer the snapshot withdraws the
        authority."""
        effective = effective_approval(job, "merge")
        if job.state is not JobState.AWAITING_MERGE_APPROVAL:
            return [f"{job.job_id} became {job.state.value} while the write was being authorized"]
        if effective is None or effective["id"] != approval["id"] or time_problems(effective, now_utc()):
            return ["the effective merge approval was replaced or expired while the write was being authorized"]
        intent = open_merge_attempt(job)
        if intent is None or intent["id"] != write.attempt or intent["action"] != write.operation \
                or attempt_dispatch(job, intent) is not None:
            return [f"the intent {write.attempt} is no longer open and undispatched"]
        problems = self._intent_problems(job, approval, write, config)
        if problems:
            return [f"while the write was being authorized: {problem}" for problem in problems]
        if write.operation == "create-pr":
            history = creation_history_problems(self.jobs.list(), job)
            if history:
                return history
        try:
            changed = not signers or verifier.signers_digest() != signers
        except ApprovalRefused:
            changed = True
        if changed:
            return ["the allowed-signers file changed (or cannot be read) after the signatures were verified: a key "
                    "may have been revoked"]
        try:
            current = configuration_fingerprint(self.anchors.configuration())
        except Exception as exc:  # pylint: disable=broad-exception-caught  # unreadable is never "unchanged"
            return [f"the project configuration cannot be read again before the dispatch ({type(exc).__name__}: "
                    f"{str(exc)[:200]}): nothing is sent"]
        if current != snapshot:
            return ["the project configuration changed while the write was being authorized: nothing is sent under a "
                    "mixture of policies — run the merge again to decide under the new configuration"]
        return []
