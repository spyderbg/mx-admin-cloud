"""W4.7 (M1): integration-job intake — validate an existing branch (or PR) by immutable SHA and record the job.

    python -m feature_coordinator integration intake --source-repo OWNER/NAME --source-ref BRANCH --source-sha SHA
        --base dev --base-sha SHA --issue N [--pr N] [--supersede JOB] [--apply] [--json]

The intake is READ-ONLY towards Git and GitHub: `git ls-remote origin`, and `rev-parse`, `rev-list`, `cat-file`,
`merge-base --is-ancestor` and `config`/`remote get-url` reads in the checkout (ReadOnlyGit), and one `GET pull
request` when `--pr` is given. It never fetches — explicitly or lazily (partial clones) — creates, moves or pushes a
ref, never touches the working tree or index, and never creates or edits a PR. Only the coordinator state directory
is written (with --apply). Local reads ignore replace refs, grafts and the commit-graph, and provenance is proven
from hash-verified commit objects (ReadOnlyGit.verified_range), never from a view of the history that the checkout
could have rewritten or truncated.

Order (design §3, §4 INTAKE):

  0. the request is well-formed (IntakeRequest: full 40-hex SHAs, plain branch names, owner/name, positive numbers)
     — else nothing is recorded; with --apply `github.integration.enabled` must be true; the checkout's `origin`
     must be exactly `github.repository` (one URL, not rewritten), its Git configuration must not run programs
     (gitsafe), and it must not be shallow, grafted, non-SHA-1, or a partial clone that Git cannot stop from
     fetching (ReadOnlyGit.repository_problem) — else nothing is recorded (an environment problem, not a property
     of the request);
  1. under coordinator.lock: the same request (IntakeRequest.key) already has a job → that job is returned
     (idempotent; a job still in INTAKE is resumed). Another ACTIVE job holding the same source branch, source
     commit or PR → refused, nothing recorded. Otherwise a job is recorded in INTAKE (write-ahead);
  2. without any lock, the checks: policy (the source repository is `github.repository` — the M1 allowlist; the base
     is the configured base branch; source ≠ base) → origin's refs are exactly the stated SHAs → the checkout is
     still fit (repository_problem) and both commits are present locally → the source adds at least one commit to
     the base, at most MAX_INTAKE_COMMITS, the range is proven complete and unmodified from hash-verified commit
     objects, and every commit is authored by a `github.integration.known_authors` address (provenance) → the PR,
     if given, is open, from source_repo:source_ref at source_sha, into base_ref;
  3. under coordinator.lock again, only if the job is still in INTAKE: every check passed → AWAITING_APPROVAL with
     the provenance (and PR facts); a check failed → REFUSED with `attention` (every reason and the observed SHAs);
     a read could not complete or prove the history (remote or GitHub unavailable, commits not fetched, a missing
     ancestor, a local view that differs from the commit objects) → the job STAYS in INTAKE with `deferred` set:
     repeat the same command once the cause is fixed.

A terminal job is returned as it is. A new attempt with identical inputs needs `--supersede <that job>` (the new job
records it); repeating that command returns the same new job. There is no force option.
"""

from __future__ import annotations

import collections
import contextlib
import functools
import heapq
import os
import re
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

from .config import ProjectConfig
from .errors import CoordinatorError, GitHubError, GitHubNotFoundError, GitOperationError, RemoteUnavailableError
from .gitsafe import git_argv, hardened_env
from .integration import (
    IntakeRequest,
    IntegrationError,
    IntegrationJob,
    IntegrationJobs,
    JobState,
    SHA_RE,
    clean_text,
    job_summary,
)
from .integration_isolation import IsolatedRepository, assert_no_execution_config_strict, isolated_remote_head
from .integration_objects import (  # re-exported: M1's names stay available here
    MAX_COMMIT_OBJECT,
    CommitObject,
    HistoryRefused,
    HistoryUnverifiable,
    Identity,
    ObjectMissing,
    _CommitReader,
    commit_object_sha,
    object_sha,
    parse_commit,
)
from .issue_sync import SimulatedCrash
from .locks import LockSet
from .model import utc_now
from .publish import github_repository_of

__all__ = [  # the public names, including those re-exported from integration_objects (M1 imports them from here)
    "CheckResult", "CommitObject", "HistoryRefused", "HistoryUnverifiable", "Identity", "IntakeReport",
    "IntegrationIntake", "LOCAL_GIT_ARGS", "LOCAL_GIT_ENV", "MAX_BASE_WALK", "MAX_COMMIT_OBJECT", "MAX_INTAKE_COMMITS",
    "MAX_LISTED", "NO_GRAFT_FILE", "ObjectMissing", "READ_ONLY_SUBCOMMANDS", "ReadOnlyGit", "VerifiedRange",
    "check_actor", "commit_object_sha", "lazy_fetch_switch_supported", "object_sha", "parse_commit",
]

MAX_INTAKE_COMMITS = 2000      # a larger range is refused (split the source), never truncated (design §6.3)
MAX_BASE_WALK = 100_000        # base commits read to prove the range's boundary parents are in base_sha
MAX_LISTED = 20                # identities and reasons shown per list in a record
# The only Git subcommands ReadOnlyGit runs on the checkout. `ls-remote` (origin's refs) goes through
# GitCoordinator.remote_head, which bounds it and kills its process group on timeout.
READ_ONLY_SUBCOMMANDS = frozenset({"rev-parse", "rev-list", "cat-file", "merge-base", "config", "remote"})

# Trust in the LOCAL history (Codex review of M1, findings 1–3). Every local intake command runs with:
#  * no replace refs: `--no-replace-objects` + GIT_NO_REPLACE_OBJECTS=1 + core.useReplaceRefs=false (a replace ref
#    would show another commit's author for a pinned SHA);
#  * no grafts: GIT_GRAFT_FILE points at a path that cannot exist (an inherited GIT_GRAFT_FILE is overridden); a
#    graft file in the checkout is also refused before the checks (repository_problem);
#  * no commit-graph (core.commitGraph=false): parents are read from the commit objects, never from a cache file;
#  * no lazy fetch: GIT_NO_LAZY_FETCH=1 (honoured where Git supports it) AND every transport refused
#    (protocol.allow=never, protocol.<name>.allow=never for each built-in transport, GIT_PROTOCOL_FROM_USER=0), so
#    a partial clone's promisor fetch fails instead of downloading; a partial clone is accepted only when Git has
#    `--no-lazy-fetch` (Git ≥ 2.44), otherwise refused (fail closed, repository_problem);
#  * no automatic maintenance (gc.auto=0, maintenance.auto=false) and no optional locks (gitsafe).
# Shallow checkouts are refused. Commit objects are read raw (`cat-file --batch`, length-delimited), each one's SHA-1
# is recomputed, and they are parsed strictly (parse_commit) — no field delimiter that a commit could contain.
NO_GRAFT_FILE = "/dev/null/no-grafts"   # a path below a character device can never exist (ENOTDIR, no warning)
_NO_FETCH_PROTOCOLS = ("file", "git", "ssh", "http", "https", "ftp", "ftps", "ext", "fd")
LOCAL_GIT_ARGS: tuple[str, ...] = (
    "--no-replace-objects",
    "-c", "core.useReplaceRefs=false",
    "-c", "core.commitGraph=false",
    "-c", "gc.auto=0",
    "-c", "maintenance.auto=false",
    "-c", "protocol.allow=never",
    *(arg for name in _NO_FETCH_PROTOCOLS for arg in ("-c", f"protocol.{name}.allow=never")),
)
LOCAL_GIT_ENV: dict[str, str] = {"GIT_NO_LAZY_FETCH": "1", "GIT_NO_REPLACE_OBJECTS": "1",
                                 "GIT_PROTOCOL_FROM_USER": "0", "GIT_GRAFT_FILE": NO_GRAFT_FILE}
# Inherited variables that would redirect the history view (removed before LOCAL_GIT_ENV is applied).
_UNSET_ENV = frozenset({"GIT_SHALLOW_FILE", "GIT_REPLACE_REF_BASE", "GIT_QUARANTINE_PATH"})


@functools.lru_cache(maxsize=1)
def lazy_fetch_switch_supported() -> bool:
    """True when this Git has `--no-lazy-fetch` (Git ≥ 2.44): then GIT_NO_LAZY_FETCH=1 is known to be honoured by
    every local command. Without it the no-fetch guarantee for a partial clone cannot be established (some
    distributions backport the variable without the option; they are treated as not supporting it)."""
    try:
        result = subprocess.run(["git", "--no-lazy-fetch", "version"], capture_output=True, text=True,
                                env=hardened_env(), timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and result.stdout.startswith("git version")


@dataclass
class VerifiedRange:
    """base..head as proven from hash-verified commit objects (ReadOnlyGit.verified_range)."""

    commits: list[CommitObject]
    boundary_parents: int
    base_commits_read: int


class _IsolatedRemote:  # pylint: disable=too-few-public-methods
    """ReadOnlyGit's remote reader: `ls-remote` of the URL validated at preflight, run in the private repository
    (integration_isolation.py), never with the checkout as the repository."""

    def __init__(self, git: "ReadOnlyGit") -> None:
        self._owner = git

    def remote_head(self, branch: str, *, timeout: float) -> str | None:
        """origin's commit for refs/heads/<branch> (None: no such branch); RemoteUnavailableError on failure."""
        url = self._owner.origin_url()
        return isolated_remote_head(self._owner.isolated(), url, branch, timeout=timeout)


class ReadOnlyGit:
    """The only Git access of the intake and the inspection: read-only queries, every one under LOCAL_GIT_ARGS /
    LOCAL_GIT_ENV (no replace refs, grafts, commit-graph, lazy fetch or maintenance) on top of the gitsafe hardening.
    Exposes no mutation.

    M2 (integration_isolation.py): every query that reads objects (`rev-list`, `cat-file`, `merge-base`) or a remote
    (`ls-remote`) runs in a private repository whose only link to the checkout is `objects/info/alternates`; only the
    preflight queries that inspect the checkout itself (`config`, `remote get-url`, `rev-parse`) run on it. Use
    `with git.session():` around one run so the private repository is removed afterwards."""

    def __init__(self, repo: Path, config: ProjectConfig, locks: LockSet, *, timeout: float) -> None:
        self.root = Path(repo)
        self.config = config
        self.timeout = timeout
        self.scratch = locks.dir.parent / "tmp"
        self._git = _IsolatedRemote(self)   # its remote_head is the only remote access (tests replace it)
        self._checked = False
        self._origin_url: str | None = None
        self._isolated: IsolatedRepository | None = None
        self._sessions = 0

    def _guard(self) -> None:
        """Refuse a checkout whose own Git configuration names a program: fail closed before the first command
        (strict superset of gitsafe layer 2). Since M2 no object or remote query reads that configuration (the
        private repository), so this is a policy check, repeated before any decision is recorded (recheck)."""
        if not self._checked:
            assert_no_execution_config_strict(self.root, where="the checkout (integration job)", timeout=self.timeout)
            self._checked = True

    def recheck(self) -> None:
        """The strict configuration scan AGAIN (right before a decision is recorded): a checkout changed during the
        run to name a program is refused as tampered (GitConfigExecutionError), even though no program could run."""
        assert_no_execution_config_strict(self.root, where="the checkout (integration job, changed during the run)",
                                          timeout=self.timeout)

    # -- the private repository (M2) ---------------------------------------------------------------

    @contextlib.contextmanager
    def session(self) -> Iterator["ReadOnlyGit"]:
        """One run: the private repository lives until the outermost session ends, then it is removed."""
        self._sessions += 1
        try:
            yield self
        finally:
            self._sessions -= 1
            if not self._sessions:
                self.close()

    def close(self) -> None:
        """Remove the private repository (idempotent; a later query creates a new one)."""
        if self._isolated is not None:
            self._isolated.close()
            self._isolated = None

    def isolated(self) -> IsolatedRepository:
        """The private repository of this run (created on first use, linked to the checkout's object directory)."""
        if self._isolated is None:
            self._guard()
            common = Path(os.path.join(self.root, self._run_checkout("rev-parse", "--git-common-dir").strip()))
            objects = Path(os.path.realpath(common / "objects"))
            self._isolated = IsolatedRepository(objects, scratch=self.scratch)
            self._isolated.open()
        return self._isolated

    def origin_url(self) -> str:
        """origin's URL as validated by origin_problem (read once; never re-read from the checkout)."""
        if self._origin_url is None:
            problem = self.origin_problem()
            if problem or self._origin_url is None:
                raise RemoteUnavailableError(f"origin cannot be used: {problem or 'not validated'}")
        return self._origin_url

    @staticmethod
    def _env() -> dict[str, str]:
        env = {k: v for k, v in os.environ.items() if k not in _UNSET_ENV}
        env.update(LOCAL_GIT_ENV)
        return hardened_env(env)

    @staticmethod
    def _local(*args: str) -> tuple[str, ...]:
        """LOCAL_GIT_ARGS + *args*, for a subcommand in READ_ONLY_SUBCOMMANDS only."""
        if not args or args[0] not in READ_ONLY_SUBCOMMANDS:
            raise GitOperationError(f"integration intake: git {args[0] if args else ''} is not a read-only query")
        return (*LOCAL_GIT_ARGS, *args)

    def _query(self, *args: str, stdin: str | None = None, checkout: bool = False) -> subprocess.CompletedProcess:
        """A read-only query: in the private repository (default) or, for *checkout* preflight queries
        (config/remote/rev-parse — no object access), on the checkout itself."""
        local = self._local(*args)
        self._guard()
        if checkout:
            if args[0] not in ("config", "remote", "rev-parse"):
                raise GitOperationError(f"integration: git {args[0]} never runs on the checkout itself")
            argv = git_argv(self.root, *local)
        else:
            argv = self.isolated().argv(*local)
        try:
            return subprocess.run(argv, capture_output=True, text=True, input=stdin, timeout=self.timeout,
                                  env=self._env(), check=False)
        except subprocess.TimeoutExpired as exc:
            raise GitOperationError(f"git {args[0]} did not finish within {self.timeout:.0f}s") from exc

    def _run(self, *args: str, stdin: str | None = None) -> str:
        result = self._query(*args, stdin=stdin)
        if result.returncode != 0:
            raise GitOperationError(f"git {' '.join(args[:2])} failed: {clean_text(result.stderr, 200)}")
        return result.stdout

    def _run_checkout(self, *args: str) -> str:
        result = self._query(*args, checkout=True)
        if result.returncode != 0:
            raise GitOperationError(f"git {' '.join(args[:2])} failed: {clean_text(result.stderr, 200)}")
        return result.stdout

    def _lines(self, *args: str) -> tuple[str, ...]:
        """Non-empty output lines of a checkout query; a failing query (e.g. an unset key) is empty."""
        result = self._query(*args, checkout=True)
        return tuple(line.strip() for line in result.stdout.splitlines() if line.strip()) \
            if result.returncode == 0 else ()

    # -- the checkout ------------------------------------------------------------------------------

    def origin_problem(self) -> str | None:
        """Why `origin` is not exactly `github.repository` (None: it is; its URL is then kept for remote reads)."""
        configured = self._lines("config", "--get-all", "remote.origin.url")
        fetch = self._lines("remote", "get-url", "--all", "origin") if configured else ()
        push = self._lines("remote", "get-url", "--push", "--all", "origin") if configured else ()
        wanted = f"{self.config.github.hostname}/{self.config.github.repository}"
        if len(configured) != 1:
            return f"origin must have exactly one URL (it has {len(configured)}); expected {wanted}"
        if fetch != configured or push != configured:
            return f"origin's URL is rewritten (insteadOf/pushurl); refs would not be read from {wanted}"
        repository = github_repository_of(configured[0], self.config.github.hostname)
        if repository is None or repository.lower() != self.config.github.repository.lower():
            return f"origin ({clean_text(configured[0], 120)!r}) is not {wanted}"
        self._origin_url = configured[0]
        return None

    def repository_problem(self) -> str | None:
        """Why this checkout's local history cannot prove provenance, or why a read could fetch implicitly (None:
        neither). Refused: a shallow clone, a graft file, an object format other than SHA-1, and a partial clone
        (promisor remote) when Git cannot be told never to fetch (no `--no-lazy-fetch`)."""
        if self._run_checkout("rev-parse", "--is-shallow-repository").strip() != "false":
            return ("the checkout is a shallow clone: commits beyond its boundary cannot be checked; fetch the full "
                    "history (`git fetch --unshallow origin`)")
        common = self.root / self._run_checkout("rev-parse", "--git-common-dir").strip()
        for name, why in (("shallow", "a shallow boundary file"), ("info/grafts", "a graft file")):
            if os.path.lexists(common / name):
                return (f"the checkout has {why} ({name}), which rewrites the history locally: not supported "
                        "for provenance; remove it")
        object_format = self._run_checkout("rev-parse", "--show-object-format").strip()
        if object_format != "sha1":
            return f"the checkout uses the {clean_text(object_format, 20)!r} object format; M1 supports SHA-1 only"
        promisors = self.promisor_remotes()
        if promisors and not lazy_fetch_switch_supported():
            return (f"the checkout is a partial clone (promisor remote(s) {', '.join(promisors)}) and this Git has "
                    "no `--no-lazy-fetch` (Git < 2.44): the intake cannot guarantee that a read never fetches "
                    "missing objects, so it refuses (use a full clone)")
        return None

    def promisor_remotes(self) -> list[str]:
        """Remotes Git may lazily fetch missing objects from: `extensions.partialClone` and every
        `remote.<name>.promisor` that is not explicitly false (effective configuration, includes resolved)."""
        names = set()
        for entry in self._run_checkout("config", "--list", "-z").split("\0"):
            key, _sep, value = entry.partition("\n")
            key = key.strip()
            if key.lower() == "extensions.partialclone":
                names.add(clean_text(value, 80) or "?")
            elif key.lower().startswith("remote.") and key.lower().endswith(".promisor") \
                    and value.strip().lower() not in ("false", "no", "off", "0"):
                names.add(clean_text(key[len("remote."):-len(".promisor")], 80))
        return sorted(names)

    # -- queries -----------------------------------------------------------------------------------

    def remote_head(self, ref: str) -> str | None:
        """origin's commit for refs/heads/<ref> (None: no such branch). RemoteUnavailableError on failure."""
        self._guard()
        return self._git.remote_head(ref, timeout=self.timeout)

    def object_type(self, sha: str) -> str | None:
        """The type of the local object *sha* (None: not present; nothing is fetched)."""
        _require_sha(sha)
        out = self._run("cat-file", "--batch-check", stdin=f"{sha}\n")
        if out == f"{sha} missing\n":
            return None
        match = re.fullmatch(r"([0-9a-f]{40}) ([a-z]{1,10}) ([0-9]{1,12})\n", out)
        if match is None or match.group(1) != sha:
            raise HistoryUnverifiable(f"unexpected `git cat-file --batch-check` output for {sha[:12]}: "
                                      f"{clean_text(out, 120)!r}")
        return match.group(2)

    def commit_exists(self, sha: str) -> bool:
        """True when the commit object is present locally (nothing is fetched)."""
        return self.object_type(sha) == "commit"

    def is_ancestor(self, ancestor: str, descendant: str) -> bool:
        """True when *ancestor* is contained in *descendant*; GitOperationError when Git cannot tell."""
        _require_sha(ancestor)
        _require_sha(descendant)
        result = self._query("merge-base", "--is-ancestor", ancestor, descendant)
        if result.returncode in (0, 1):
            return result.returncode == 0
        self._raise_if_incomplete(descendant)
        raise GitOperationError(f"git merge-base --is-ancestor failed: {clean_text(result.stderr, 200)}")

    def merge_bases(self, base: str, head: str) -> list[str]:
        """Git's merge bases of *base* and *head* (`merge-base --all`; empty: no common history). The caller must
        prove each one against hash-verified data (M2: they must be boundary parents of the verified range)."""
        _require_sha(base)
        _require_sha(head)
        result = self._query("merge-base", "--all", base, head)
        if result.returncode == 1 and not result.stdout.strip():
            return []
        if result.returncode != 0:
            self._raise_if_incomplete(head)
            raise GitOperationError(f"git merge-base --all failed: {clean_text(result.stderr, 200)}")
        shas = result.stdout.split()
        if not shas or not all(SHA_RE.fullmatch(sha) for sha in shas) or len(set(shas)) != len(shas):
            raise HistoryUnverifiable(f"unexpected `git merge-base --all` output {clean_text(result.stdout, 120)!r}")
        return shas

    def range_commits(self, base: str, head: str, *, limit: int) -> list[tuple[str, tuple[str, ...]]]:
        """Git's view of base..head: (commit, parents) per line of `rev-list --parents`, at most *limit*. Strictly
        parsed (40-hex tokens only, no duplicate commit); anything else is HistoryUnverifiable."""
        _require_sha(base)
        _require_sha(head)
        try:
            out = self._run("rev-list", "--parents", f"--max-count={limit}", head, f"^{base}", "--")
        except GitOperationError:
            self._raise_if_incomplete(head)
            raise
        if out and not out.endswith("\n"):
            raise HistoryUnverifiable("`git rev-list` output does not end with a newline (truncated?)")
        rows: list[tuple[str, tuple[str, ...]]] = []
        for line in out.split("\n")[:-1]:
            tokens = line.split(" ")
            if not all(SHA_RE.fullmatch(t) for t in tokens):
                raise HistoryUnverifiable(f"unexpected `git rev-list` output line {clean_text(line, 120)!r}")
            rows.append((tokens[0], tuple(tokens[1:])))
        if len({sha for sha, _ in rows}) != len(rows):
            raise HistoryUnverifiable("`git rev-list` listed a commit twice")
        return rows

    def missing_parent(self, start: str, *, limit: int = 5000) -> tuple[str, str] | None:
        """(child, parent) for the first commit reachable from *start* whose parent object is not in the checkout,
        walking hash-verified commit objects (at most *limit*); None when none is found (or the walk cannot go on)."""
        child_of: dict[str, str | None] = {start: None}
        queue = collections.deque([start])
        with self.reader() as reader:
            while queue and reader.count < limit:
                sha = queue.popleft()
                try:
                    commit = reader.read(sha, strict=False)
                except ObjectMissing:
                    child = child_of[sha]
                    return (child, sha) if child is not None else None
                except HistoryUnverifiable:
                    return None
                for parent in commit.parents:
                    if parent not in child_of:
                        child_of[parent] = sha
                        queue.append(parent)
        return None

    def _raise_if_incomplete(self, start: str) -> None:
        """After Git failed to walk from *start*: HistoryUnverifiable naming the commit whose parent is missing,
        when that is the cause (in the private repository a checkout's shallow boundary is such a missing parent)."""
        try:
            found = self.missing_parent(start)
        except (CoordinatorError, OSError):
            return
        if found is not None:
            child, parent = found
            raise HistoryUnverifiable(f"commit {child[:12]} names parent {parent[:12]}, which is not present in this "
                                      "checkout: the local history is incomplete or was rewritten (shallow boundary, "
                                      "graft, partial or damaged clone) and cannot be used for provenance; fetch the "
                                      "full history")

    def reader(self) -> _CommitReader:
        """A hash-verifying object reader over the checkout's objects, run in the private repository (use as a
        context manager)."""
        self._guard()
        return _CommitReader(self.isolated().argv(*self._local("cat-file", "--batch")), self._env(), self.timeout)

    def verified_range(self, base: str, head: str, *, limit: int = MAX_INTAKE_COMMITS,
                       base_limit: int = MAX_BASE_WALK) -> VerifiedRange:
        """Every commit in base..head, proven complete and unmodified:

          1. Git lists base..head (`rev-list --parents`; at most *limit*, else HistoryRefused);
          2. every listed commit object is read raw, its SHA-1 recomputed and parsed strictly, and the parents Git
             listed must be exactly the parents in the object (a shallow boundary, a graft or a corrupt cache
             shows here);
          3. head is listed and base is not;
          4. closure: every parent of a listed commit that is not listed itself must be an ancestor of base, proven
             by walking base's hash-verified commit objects (at most *base_limit*, else HistoryRefused).

        Then no commit reachable from head and not from base can be missing from the list: each one is reached
        from head through verified parent links, and the first link that leaves the list ends in base.
        HistoryUnverifiable (defer) when the local objects cannot prove it; HistoryRefused for a malformed commit."""
        listed = self.range_commits(base, head, limit=limit + 1)
        if len(listed) > limit:
            raise HistoryRefused(f"the source adds more than {limit} commits; split it into several jobs (a range "
                                 "is refused, never truncated)")
        if not listed:
            raise HistoryUnverifiable("Git lists no commit in base..source although source is not contained in "
                                      "base: the local history is inconsistent")
        with self.reader() as reader:
            commits: dict[str, CommitObject] = {}
            for sha, parents in listed:
                commit = reader.read(sha, strict=True)
                if commit.parents != parents:
                    raise HistoryUnverifiable(
                        f"Git lists the parents of {sha[:12]} as {_short(parents)}, but the commit object names "
                        f"{_short(commit.parents)}: the checkout's view of the history is rewritten (shallow "
                        "boundary, graft or corrupt object store) and cannot be used for provenance")
                commits[sha] = commit
            if head not in commits or base in commits:
                raise HistoryUnverifiable("Git's base..source list does not start at source or includes base: the "
                                          "local history is inconsistent")
            boundary = sorted({p for c in commits.values() for p in c.parents if p not in commits})
            walked = self._prove_in_base(reader, base, boundary, limit=base_limit)
        return VerifiedRange(commits=[commits[sha] for sha, _ in listed], boundary_parents=len(boundary),
                             base_commits_read=walked)

    @staticmethod
    def _prove_in_base(reader: _CommitReader, base: str, targets: list[str], *, limit: int) -> int:
        """Walk base's verified commit objects (newest committer time first) until every *target* is found.
        Returns the number of base commits read."""
        remaining = set(targets) - {base}
        if not targets:
            return 0
        nodes = {base: reader.read(base, strict=False)}
        queue = [(-nodes[base].committer.time, base)]
        while queue and remaining:
            _time, sha = heapq.heappop(queue)
            for parent in nodes[sha].parents:
                if parent in nodes:
                    continue
                remaining.discard(parent)
                if not remaining:
                    break
                if len(nodes) >= limit:
                    raise HistoryRefused(f"the source forks from the base more than {limit} base commits back: "
                                         "its range cannot be proven complete; merge or rebase it onto a recent "
                                         "base first")
                nodes[parent] = reader.read(parent, strict=False)
                heapq.heappush(queue, (-nodes[parent].committer.time, parent))
        if remaining:
            raise HistoryUnverifiable(f"parent(s) {_short(sorted(remaining))} of the range are not contained in "
                                      "base_sha according to its commit objects, although Git excluded them: the "
                                      "local history is inconsistent or rewritten")
        return len(nodes)


def _require_sha(value: str) -> None:
    if not SHA_RE.fullmatch(value or ""):
        raise GitOperationError("integration intake Git queries take full commit SHAs only")


def _short(shas: Iterable[str]) -> str:
    items = [sha[:12] for sha in shas]
    return "[" + ", ".join(items[:5]) + (", ..." if len(items) > 5 else "") + "]"


@dataclass
class CheckResult:
    """What the read-only checks found."""

    refusals: list[str] = field(default_factory=list)
    deferred: str = ""
    provenance: dict[str, Any] | None = None
    pr: dict[str, Any] | None = None
    observed: dict[str, Any] = field(default_factory=dict)
    checks: list[dict[str, str]] = field(default_factory=list)

    def note(self, name: str, status: str, detail: str = "") -> None:
        """Record one check: pass | fail | deferred | skipped."""
        self.checks.append({"check": name, "status": status, "detail": clean_text(detail, 300)})

    def refuse(self, name: str, reason: str) -> None:
        """A definitive failure: the job will be REFUSED."""
        self.refusals.append(clean_text(reason))
        self.note(name, "fail", reason)

    def defer(self, name: str, reason: str) -> None:
        """A read that could not complete: the job stays in INTAKE."""
        self.deferred = self.deferred or clean_text(reason)
        self.note(name, "deferred", reason)


@dataclass
class IntakeReport:  # pylint: disable=too-many-instance-attributes
    """What one intake did (apply) or would do (dry run)."""

    applied: bool
    outcome: str = ""             # accepted | refused | deferred | existing | changed-meanwhile | would-*
    created: bool = False
    job: dict[str, Any] | None = None
    checks: list[dict[str, str]] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    deferred: str = ""
    messages: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True when the request is (or would be) an active job past INTAKE."""
        if self.outcome == "would-accept":
            return True
        state = (self.job or {}).get("state")
        return self.outcome in ("accepted", "existing") and state not in (None, JobState.INTAKE.value) \
            and not (self.job or {}).get("terminal")

    def to_dict(self) -> dict[str, Any]:
        """JSON form."""
        return {**asdict(self), "ok": self.ok}


class IntegrationIntake:
    """Intake of integration jobs for one checkout and state directory (see the module docstring)."""

    def __init__(self, store: Any, config: ProjectConfig, git: ReadOnlyGit, *,
                 pr_client: Callable[[], Any] | None = None, faults: Iterable[str] = ()) -> None:
        self.store, self.config, self.git = store, config, git
        self.jobs = IntegrationJobs(store)
        self.pr_client = pr_client
        self.faults = set(faults)

    def _fault(self, name: str) -> None:
        if name in self.faults:
            self.faults.discard(name)
            raise SimulatedCrash(name)

    # -- the run -----------------------------------------------------------------------------------

    def run(self, request: IntakeRequest, *, apply: bool, actor: str = "operator",
            supersede: str | None = None) -> IntakeReport:
        """Check *request*; with *apply*, record (or resume) its job and move it out of INTAKE when the checks
        decide. Raises IntegrationError (nothing recorded) for a precondition that is not the request's fault."""
        with self.git.session():  # M2: the private repository of this run is removed when it ends
            return self._run(request, apply=apply, actor=actor, supersede=supersede)

    def _run(self, request: IntakeRequest, *, apply: bool, actor: str, supersede: str | None) -> IntakeReport:
        report = IntakeReport(applied=apply)
        if apply and not self.config.github.integration.enabled:
            raise IntegrationError("github.integration.enabled is false: integration jobs are recorded only once an "
                                   "operator enables them in the project configuration (dry runs work without it)")
        problem = self.git.origin_problem()
        if problem:
            raise IntegrationError(f"cannot resolve refs for an integration intake: {problem}; nothing was recorded")
        problem = self.git.repository_problem()
        if problem:
            raise IntegrationError(f"this checkout cannot prove the provenance of an integration: {problem}; "
                                   "nothing was recorded")
        if not apply:
            return self._dry_run(request, supersede, report)
        with self.store.transaction():
            job, report.created = self._claim(request, supersede, actor)
        if job.state is not JobState.INTAKE:
            return self._existing(job, report)
        self._fault("after-create")
        result = self.check(request)
        self._fault("after-checks")
        self.git.recheck()  # M2: a checkout changed during the run to name a program is refused; nothing recorded
        return self._decide(job.job_id, result, actor, report)

    def _dry_run(self, request: IntakeRequest, supersede: str | None, report: IntakeReport) -> IntakeReport:
        existing = self._admit(request, supersede)  # no lock: a preview; --apply decides again under the lock
        if existing is not None and existing.state is not JobState.INTAKE:
            return self._existing(existing, report)
        result = self.check(request)
        report.checks, report.reasons, report.deferred = result.checks, result.refusals, result.deferred
        report.outcome = ("would-refuse" if result.refusals else "would-defer" if result.deferred else "would-accept")
        if existing is not None:
            report.job = job_summary(existing)
            report.messages.append(f"--apply would resume {existing.job_id} (in INTAKE since an earlier intake)")
        else:
            report.messages.append("dry run — nothing was recorded (pass --apply to record the job)")
        return report

    def _lookup(self, request: IntakeRequest, supersede: str | None) -> IntegrationJob | None:
        """The job this request maps to without creating one (dry run; also the first step of _claim)."""
        same = self.jobs.find(key=request.key)
        if supersede is not None:
            target = self.jobs.load(supersede)
            if target.request_key != request.key:
                raise IntegrationError(f"--supersede {supersede}: that job has different inputs; only a job with "
                                       "exactly this request can be superseded")
            if not target.terminal:
                return target
            successors = self.jobs.find(supersedes=supersede)
            if successors:
                return successors[-1]
            active = [job for job in same if not job.terminal]
            return active[-1] if active else None
        active = [job for job in same if not job.terminal]
        if active:
            return active[-1]
        return same[-1] if same else None

    def _admit(self, request: IntakeRequest, supersede: str | None) -> IntegrationJob | None:
        """The job this request maps to (None: a new job would be created), or IntegrationError when another ACTIVE
        job holds the same source branch, source commit or PR. Reads only: the dry run's preview and, inside the
        transaction, the authoritative decision of _claim."""
        existing = self._lookup(request, supersede)
        if existing is not None:
            return existing
        conflicts = self.jobs.active_conflicts(request)
        if conflicts:
            shown = ", ".join(f"{job.job_id} ({job.state.value}, {job.source['ref']}@{job.source['sha'][:12]})"
                              for job in conflicts[:5])
            raise IntegrationError(f"an active integration job already holds this source branch, source commit or "
                                   f"PR: {shown}; abort it first (`integration abort --job ...`). Nothing was "
                                   "recorded")
        return None

    def _claim(self, request: IntakeRequest, supersede: str | None, actor: str) -> tuple[IntegrationJob, bool]:
        """(Inside the transaction) the existing job of this request, or a new job in INTAKE."""
        existing = self._admit(request, supersede)
        if existing is not None:
            return existing, False
        job = IntegrationJob.new(self.jobs.next_id(), request, actor=actor, supersedes=supersede,
                                 reason="intake request recorded" + (f" (supersedes {supersede})" if supersede else ""))
        self.jobs.save(job)
        self.store.append_event("integration-intake", job=job.job_id, actor=job.history[0]["actor"],
                                source=job.source, base=job.base, issue=job.issue, pr=(job.pr or {}).get("number"),
                                supersedes=supersede)
        return job, True

    def _existing(self, job: IntegrationJob, report: IntakeReport) -> IntakeReport:
        report.outcome, report.job = "existing", job_summary(job)
        report.messages.append(f"{job.job_id} already exists for exactly this request ({job.state.value}); nothing "
                               "was changed")
        if job.terminal:
            report.messages.append(f"{job.job_id} is terminal and is never reopened; for a new attempt with these "
                                   f"inputs pass --supersede {job.job_id}")
        return report

    def _decide(self, job_id: str, result: CheckResult, actor: str, report: IntakeReport) -> IntakeReport:
        report.checks, report.reasons, report.deferred = result.checks, result.refusals, result.deferred
        if result.refusals:
            attention = {"reason": result.refusals[0], "reasons": result.refusals[:MAX_LISTED],
                         "observed": result.observed, "since": utc_now()}
            job, changed = self.jobs.transition(job_id, JobState.REFUSED, "; ".join(result.refusals), actor=actor,
                                                expect=(JobState.INTAKE,), attention=attention)
            report.outcome = "refused" if changed else self._settled(job, JobState.REFUSED)
        elif result.deferred:
            with self.store.transaction():
                job = self.jobs.load(job_id)
                changed = job.state is JobState.INTAKE
                if changed:
                    job.deferred = {"reason": result.deferred, "at": utc_now()}
                    self.jobs.save(job)
                    self.store.append_event("integration-intake-deferred", job=job_id, reason=result.deferred)
            report.outcome = "deferred" if changed else "changed-meanwhile"
        else:
            job, changed = self.jobs.transition(job_id, JobState.AWAITING_APPROVAL,
                                                "intake checks passed: refs at the stated SHAs, authors known",
                                                actor=actor, expect=(JobState.INTAKE,), provenance=result.provenance,
                                                pr=result.pr)
            report.outcome = "accepted" if changed else self._settled(job, JobState.AWAITING_APPROVAL)
        report.job = job_summary(job)
        if report.outcome == "changed-meanwhile":
            report.messages.append(f"{job_id} changed while the checks ran (now {job.state.value}); nothing was "
                                   "changed by this run")
        elif report.outcome == "deferred":
            report.messages.append(f"{job_id} stays in INTAKE: {result.deferred} — repeat the same command later")
        return report

    @staticmethod
    def _settled(job: IntegrationJob, target: JobState) -> str:
        """Outcome when this run did not change the job: another run already moved it to the same *target*
        (`existing`), or to something else, e.g. aborted meanwhile (`changed-meanwhile`)."""
        return "existing" if job.state is target else "changed-meanwhile"

    # -- the checks (read-only) --------------------------------------------------------------------

    def check(self, request: IntakeRequest) -> CheckResult:
        """Every intake check of design §3/§4 that M1 performs. Reads only; holds no lock."""
        result = CheckResult(observed={"source_sha": request.source_sha, "base_sha": request.base_sha})
        for step in (self._policy, self._refs, self._objects):
            step(request, result)
            if result.refusals or result.deferred:
                return result
        try:
            self._provenance(request, result)
        except HistoryRefused as exc:
            result.refuse("history", str(exc))
        except (CoordinatorError, OSError) as exc:
            result.defer("history", f"the source history cannot be verified in this checkout: {exc}")
            return result
        if request.pr_number is not None:
            self._pull_request(request, result)
        return result

    def _objects(self, request: IntakeRequest, result: CheckResult) -> None:
        """The checkout is still fit (it may have changed since run() began) and holds both commits."""
        try:
            problem = self.git.repository_problem()
            types = {sha: self.git.object_type(sha) for sha in (request.source_sha, request.base_sha)}
        except (CoordinatorError, OSError) as exc:
            result.defer("history", f"the checkout could not be read: {exc}")
            return
        if problem:
            result.defer("history", problem)
            return
        missing = [sha for sha, kind in types.items() if kind is None]
        if missing:
            result.defer("objects", f"commit(s) {', '.join(m[:12] for m in missing)} are not present in this "
                                    f"checkout; fetch them (`git fetch origin {request.source_ref} "
                                    f"{request.base_ref}`) and repeat — the intake never fetches")
            return
        for sha, kind in types.items():
            if kind != "commit":
                result.refuse("objects", f"{sha} is a {kind} object, not a commit")
        if not result.refusals:
            result.note("objects", "pass", "both commits are present locally")

    def _policy(self, request: IntakeRequest, result: CheckResult) -> None:
        allowed = self.config.github.repository
        if request.source_repo.lower() != allowed.lower():
            result.refuse("allowlist", f"source repository {request.source_repo} is not permitted (M1 allowlist: "
                                       f"{allowed} only)")
        else:
            result.note("allowlist", "pass", f"{request.source_repo} is github.repository")
        if request.base_ref != self.config.base_branch:
            result.refuse("base", f"the base must be the configured base branch {self.config.base_branch!r}, not "
                                  f"{request.base_ref!r}")
        if request.source_ref == request.base_ref:
            result.refuse("base", "the source branch is the base branch")
        if request.source_sha == request.base_sha:
            result.refuse("base", "source_sha equals base_sha: there is nothing to integrate")
        if not any(c["check"] == "base" for c in result.checks):
            result.note("base", "pass", f"into {request.base_ref}")

    def _refs(self, request: IntakeRequest, result: CheckResult) -> None:
        try:
            observed = {"source_ref_sha": self.git.remote_head(request.source_ref),
                        "base_ref_sha": self.git.remote_head(request.base_ref)}
        except (CoordinatorError, OSError) as exc:
            result.defer("refs", f"origin's refs could not be read: {exc}")
            return
        result.observed.update(observed)
        for label, ref, sha, key in (("source", request.source_ref, request.source_sha, "source_ref_sha"),
                                     ("base", request.base_ref, request.base_sha, "base_ref_sha")):
            actual = observed[key]
            if actual is None:
                result.refuse(f"{label}-ref", f"{label} branch {ref!r} does not exist on origin")
            elif actual != sha:
                result.refuse(f"{label}-ref", f"{label} branch {ref!r} is at {actual} on origin, not at the stated "
                                              f"{sha}")
            else:
                result.note(f"{label}-ref", "pass", f"origin {ref} is at {sha[:12]}")

    def _provenance(self, request: IntakeRequest, result: CheckResult) -> None:
        if self.git.is_ancestor(request.source_sha, request.base_sha):
            result.refuse("range", f"source_sha {request.source_sha[:12]} is already contained in base_sha "
                                   f"{request.base_sha[:12]}: there is nothing to integrate")
            return
        history = self.git.verified_range(request.base_sha, request.source_sha, limit=MAX_INTAKE_COMMITS,
                                          base_limit=MAX_BASE_WALK)
        commits = history.commits
        result.note("range", "pass", f"{len(commits)} commit(s) to integrate")
        result.note("history", "pass", f"complete and unmodified: {len(commits)} commit object(s) hash-verified; "
                                       f"{history.boundary_parents} boundary parent(s) proven in base_sha "
                                       f"({history.base_commits_read} base commit(s) read); no replace refs, "
                                       "grafts, shallow boundary or commit-graph used")
        known = set(self.config.github.integration.known_authors)
        unknown = sorted({commit.author.key for commit in commits} - known)
        result.observed["commits"] = len(commits)
        if unknown:
            shown = [clean_text(email, 200) for email in unknown[:MAX_LISTED]]
            result.observed["unknown_authors"] = shown
            result.refuse("authors", f"commit author(s) not in github.integration.known_authors: {', '.join(shown)}")
            return
        authors = sorted({commit.author.shown() for commit in commits})
        committers = sorted({commit.committer.shown() for commit in commits})
        result.note("authors", "pass", f"{len(authors)} known author(s)")
        result.provenance = {"authors": authors[:MAX_LISTED], "committers": committers[:MAX_LISTED],
                             "commits": len(commits), "head_commit": request.source_sha,
                             "history": {"boundary_parents": history.boundary_parents,
                                         "base_commits_read": history.base_commits_read},
                             "origin": {"repo": request.source_repo, "ref": request.source_ref,
                                        "resolved_via": "git ls-remote origin", "at": utc_now()}}

    def _pull_request(self, request: IntakeRequest, result: CheckResult) -> None:
        number = request.pr_number
        assert number is not None
        if self.pr_client is None:
            result.defer("pr", f"PR #{number} cannot be read: no GitHub client")
            return
        try:
            pr = self.pr_client().get_pull_request(number)
        except GitHubNotFoundError:
            result.refuse("pr", f"PR #{number} does not exist in {self.config.github.repository}")
            return
        except (GitHubError, OSError) as exc:
            result.defer("pr", f"PR #{number} could not be read: {exc}")
            return
        problems = []
        if pr.state != "open" or pr.merged:
            problems.append(f"it is {'merged' if pr.merged else pr.state}, not open")
        if (pr.head_repo or "").lower() != request.source_repo.lower() or pr.head != request.source_ref:
            problems.append(f"its head is {pr.head_repo or '?'}:{pr.head}, not {request.source_repo}:"
                            f"{request.source_ref}")
        if pr.head_sha != request.source_sha:
            problems.append(f"its head commit is {pr.head_sha or '?'}, not source_sha {request.source_sha}")
        if pr.base != request.base_ref or (pr.base_repo or "").lower() != self.config.github.repository.lower():
            problems.append(f"its base is {pr.base_repo or '?'}:{pr.base}, not {self.config.github.repository}:"
                            f"{request.base_ref}")
        result.observed["pr"] = {"number": number, "head_sha": pr.head_sha, "head": pr.head, "base": pr.base,
                                 "state": "merged" if pr.merged else pr.state}
        if problems:
            result.refuse("pr", f"PR #{number} does not match the request: {'; '.join(problems)}")
            return
        result.note("pr", "pass", f"PR #{number} is open at source_sha into {request.base_ref}")
        result.pr = {"number": number, "head_repo": pr.head_repo, "head_ref": pr.head, "head_sha": pr.head_sha,
                     "base_repo": pr.base_repo, "base_ref": pr.base, "draft": bool(pr.draft),
                     "url": clean_text(pr.url, 200), "node_id": clean_text(pr.node_id, 100), "id": int(pr.id or 0),
                     "verified_at": utc_now()}


_ACTOR_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._@-]{0,79}")


def check_actor(value: str) -> str:
    """An operator name for the audit trail (a login-like token)."""
    if not _ACTOR_RE.fullmatch(value or ""):
        raise IntegrationError(f"--actor must be a login-like name (letters, digits, '.', '_', '@', '-'), got "
                               f"{value!r}")
    return value
