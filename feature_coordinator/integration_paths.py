"""W4.7 (M2): the full changed-path list of an integration source, read from hash-verified tree objects, and its
classification through the existing PathPolicy (design §6.2).

Which paths. An integration merges the source's COMMITS into the base (a merge commit, never a squash), so every
path whose content changes anywhere in base..source lands in the base's history — not only the paths of the net
diff. The list is therefore the union over every commit of the (hash-verified, M1) range of:

  * a commit with one parent: the paths that differ from that parent;
  * a root commit: every path of its tree;
  * a merge commit: the paths that differ from EVERY parent (what the merge itself introduced; a path taken unchanged
    from one parent was introduced by that parent's own history, which is either in the range or already in the
    base) — Git's combined-diff rule.

plus the NET diff from each merge base of base and source to the source tip: a merge commit inside the range can
take a path unchanged from an old base-side commit (a file the base deleted or hardened since), which no per-commit
rule sees, yet the integration would bring it back into the base's tree (independent review of M2, finding 1). The
merge bases come from Git but are accepted only when each one is a boundary parent of the hash-verified range (every
true merge base is), integration_inspect._paths.

A protected file added in one commit and removed in a later one is therefore still found and refused. Each path
records its kinds of change (added, deleted, modified, mode, type) and flags: `symlink` (a symbolic link is added or
changed; its target is read and checked), `gitlink` (a submodule entry is added or changed), `executable` (it becomes
executable), `git-control` (`.gitattributes` / `.gitmodules`: they change how Git checks out, diffs and merges the
tree), `renamed` (an exact rename: the same blob and mode deleted at one path and added at another in one commit;
both paths are classified anyway), `history-only` (identical in the source tip and the base tip) and `empty-tree`.

Directory entries. A changed directory is represented by the files below it (each of which carries the directory in
its path, so the directory's name is checked and classified with them), except an EMPTY tree object, which has none:
an empty subtree that is added, deleted or replaces a file is listed as a path of its own (the directory path), so it
is name-checked and classified like any other; an ADDED empty subtree is flagged `empty-tree` and refuses the job —
Git never records an empty directory (`git add` cannot), so one only comes from a crafted tree, and it would carry an
invisible entry into the base.

How. Trees and blobs are read raw through the hash-verifying reader (integration_intake._CommitReader, running in
the private repository of integration_isolation.py), each one's SHA-1 recomputed, and parsed strictly by this
module — Git's own diff machinery is not trusted with the checkout's object store. Entry names are checked for the
paths that change: not `.`/`..`/`.git` (in any case, with trailing dots or spaces), no `/`, no control, backslash or
invisible format character, valid UTF-8; anything else refuses the job (a crafted tree). Unchanged entries inherited
from the base are not judged (they are already in the base).

Classification (classify_paths): `protected` (config.protected — refused outright, never authorizable), else
`approval-required` (config.approval_required), else `owned` by a role (PathPolicy.owner_of), else `unowned`. A path
is protected (approval-required) when the path itself OR ANY OF ITS ANCESTOR DIRECTORIES matches a protected
(approval-required) pattern: `backend/src/.env/secret.py` is protected through `backend/src/.env` (`**/.env`), although
no pattern matches the full path — a protected name used as a directory cannot launder the files below it. Only the
configured patterns are consulted, so an ordinary directory is restricted only when it carries a protected name. The
classification is LEXICAL (patterns only): the live checkout's files and symlinks are never consulted. Approval-
required and unowned paths, added or changed symlinks and git-control files need an explicit per-path authorization
in the operator's signed record (design §6.2); a gitlink, a symlink whose target is absolute, leaves the repository,
enters `.git`, is or contains a protected path, a symlink kept in the source tip that does not resolve to a regular file
of that tree (a directory could expose `**/.env`; a chain, a dangling link or a submodule cannot be judged), a
malformed name, and more than MAX_CHANGED_PATHS paths refuse the job (split the source; never truncated, §6.3).
"""

from __future__ import annotations

import posixpath
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Iterable

from .config import ProjectConfig, matches
from .integration import MAX_PATH_TEXT, IntegrationError
from .integration_objects import CommitObject, HistoryRefused, HistoryUnverifiable, _CommitReader
from .paths import PathPolicy

MAX_TREE_OBJECT = 16 << 20       # a larger tree is refused (source side) or unverifiable (base side)
MAX_SYMLINK_TARGET = 4096        # a larger symlink blob is refused
MAX_CHANGED_PATHS = 2000         # more changed paths refuse the job (design §6.3: refuse, never truncate)
MAX_MERGE_DIFF_PATHS = 50_000    # per-parent diffs of a merge commit (only their intersection counts)
MAX_TREES_READ = 200_000         # tree objects read for one inspection
TREE, FILE, EXEC, LINK, GITLINK = "40000", "100644", "100755", "120000", "160000"
GIT_CONTROL_NAMES = frozenset({".gitattributes", ".gitmodules"})
INVALID_TARGET = chr(0xFFFD)     # stands for a symlink target that is not UTF-8 (always refused)
# HFS+ ignores these code points in names (zero-width and bidirectional controls): a name containing one can
# alias `.git` or display as another name. Written as code points so this source contains no invisible text.
_HFS_IGNORABLE = frozenset(chr(c) for c in (0x200C, 0x200D, 0x200E, 0x200F, 0x202A, 0x202B, 0x202C, 0x202D,
                                            0x202E, 0x206A, 0x206B, 0x206C, 0x206D, 0x206E, 0x206F, 0xFEFF))


@dataclass(frozen=True)
class Entry:
    """One tree entry: its canonical mode and object ID."""

    mode: str
    sha: str

    @property
    def kind(self) -> str:
        """tree | file | link | gitlink."""
        return {TREE: "tree", LINK: "link", GITLINK: "gitlink"}.get(self.mode, "file")


def canonical_mode(raw: bytes) -> str | None:
    """Git's canonical mode for a tree entry (legacy 100664 is a plain file), or None when it is not a valid mode."""
    text = raw.decode("ascii", "replace")
    if text in (TREE, LINK, GITLINK, FILE, EXEC):
        return text
    if re.fullmatch(r"100[0-7]{3}", text):
        return EXEC if int(text[-3:], 8) & 0o100 else FILE
    return None


def _tree_order(name: bytes, mode: str) -> bytes:
    return name + b"/" if mode == TREE else name


def parse_tree(sha: str, body: bytes, *, strict: bool) -> dict[bytes, Entry]:
    """The entries of the (hash-verified) tree object *sha*: `<mode> <name>\\0<20-byte id>` repeated, names non-empty
    without `/`, in Git's order, no duplicate. Anything else: HistoryRefused when *strict* (a source tree),
    HistoryUnverifiable otherwise (a base tree)."""
    error = HistoryRefused if strict else HistoryUnverifiable
    entries: dict[bytes, Entry] = {}
    position, previous = 0, b""
    while position < len(body):
        space = body.find(b" ", position)
        nul = body.find(b"\0", space + 1) if space >= 0 else -1
        if space < 0 or nul < 0 or nul + 21 > len(body):
            raise error(f"tree {sha[:12]} is malformed: a truncated entry")
        raw_mode = body[position:space]
        mode = canonical_mode(raw_mode)
        name = body[space + 1:nul]
        oid = body[nul + 1:nul + 21].hex()
        position = nul + 21
        if mode is None:
            raise error(f"tree {sha[:12]} is malformed: invalid mode {raw_mode[:8]!r}")
        if not name or b"/" in name:
            raise error(f"tree {sha[:12]} is malformed: an empty name or a name with '/'")
        if name in entries or _tree_order(name, mode) <= previous:
            raise error(f"tree {sha[:12]} is malformed: entries are duplicated or out of order")
        previous = _tree_order(name, mode)
        entries[name] = Entry(mode, oid)
    return entries


def name_problem(component: bytes) -> str | None:
    """Why a path component of a changed entry is not acceptable (None: it is)."""
    try:
        text = component.decode("utf-8")
    except UnicodeDecodeError:
        return "it is not UTF-8"
    if text in (".", ".."):
        return f"it is {text!r}"
    stripped = text.rstrip(". ").lower()
    if stripped == ".git" or re.fullmatch(r"git~\d+", stripped):
        return "it names Git's own directory (.git, in some spelling)"
    if "\\" in text:
        return "it contains a backslash"
    if any(unicodedata.category(ch)[0] == "C" or ch in _HFS_IGNORABLE for ch in text):
        return "it contains a control or invisible (format) character"
    return None


@dataclass
class PathChange:
    """What the range does to one path (aggregated over every commit)."""

    changes: set[str] = field(default_factory=set)
    flags: set[str] = field(default_factory=set)
    link_targets: set[str] = field(default_factory=set)   # blob IDs (then texts) of the symlink targets it is set to
    commits: int = 0
    tip_target: str | None = None     # a symlink in the source tip: the kind its target resolves to there


class TreeReader:
    """Hash-verified trees and blobs through one reader, cached by object ID, bounded."""

    def __init__(self, reader: _CommitReader, *, budget: int = MAX_TREES_READ) -> None:
        self.reader = reader
        self.budget = budget
        self._trees: dict[str, dict[bytes, Entry]] = {}

    def tree(self, sha: str, *, strict: bool) -> dict[bytes, Entry]:
        """The parsed tree *sha*."""
        if sha not in self._trees:
            if len(self._trees) >= self.budget:
                raise HistoryRefused(f"the source needs more than {self.budget} tree objects to inspect; split it")
            body = self.reader.read_object(sha, "tree", limit=MAX_TREE_OBJECT, strict=strict)
            self._trees[sha] = parse_tree(sha, body, strict=strict)
        return self._trees[sha]

    def blob(self, sha: str, *, limit: int) -> bytes:
        """The raw blob *sha* (a symlink target), at most *limit* bytes (else HistoryRefused)."""
        return self.reader.read_object(sha, "blob", limit=limit, strict=True)

    def lookup(self, root: str, path: bytes) -> Entry | None:
        """The entry at *path* in the tree *root* (None: absent)."""
        tree, parts = root, path.split(b"/")
        for index, part in enumerate(parts):
            entry = self.tree(tree, strict=False).get(part)
            if entry is None or index == len(parts) - 1:
                return entry
            if entry.mode != TREE:
                return None
            tree = entry.sha
        return None

    def diff(self, old: str | None, new: str | None, *, limit: int, prefix: bytes = b"",
             out: dict[bytes, tuple[Entry | None, Entry | None]] | None = None
             ) -> dict[bytes, tuple[Entry | None, Entry | None]]:
        """path → (old entry, new entry) for every non-tree path that differs between the trees *old* and *new*
        (None: the empty tree), plus every EMPTY subtree that is added, deleted or replaces / is replaced by a
        non-tree entry (its directory path, with its tree entry; see the module docstring). More than *limit*
        paths: HistoryRefused."""
        out = {} if out is None else out
        if old == new:
            return out
        old_entries = self.tree(old, strict=False) if old else {}
        new_entries = self.tree(new, strict=True) if new else {}
        for name in sorted(set(old_entries) | set(new_entries)):
            before, after = old_entries.get(name), new_entries.get(name)
            if before == after:
                continue
            path = prefix + name
            before_tree = before is not None and before.mode == TREE
            after_tree = after is not None and after.mode == TREE
            if before_tree or after_tree:
                old_sub = before.sha if before is not None and before_tree else None
                new_sub = after.sha if after is not None and after_tree else None
                self.diff(old_sub, new_sub, limit=limit, prefix=path + b"/", out=out)
                # a non-empty subtree is represented by its entries; an empty one (no entry) by itself
                if before_tree and (old_sub is None or self.tree(old_sub, strict=False)):
                    before = None
                if after_tree and (new_sub is None or self.tree(new_sub, strict=True)):
                    after = None
                if before is None and after is None:
                    continue
            out[path] = (before, after)
            if len(out) > limit:
                raise HistoryRefused(f"the source changes more than {limit} paths; split it into several jobs "
                                     "(a path list is refused, never truncated)")
        return out


def _kinds(before: Entry | None, after: Entry | None) -> set[str]:
    if before is None:
        return {"added"}
    if after is None:
        return {"deleted"}
    if before.kind != after.kind:
        return {"type"}
    kinds = set()
    if before.sha != after.sha:
        kinds.add("modified")
    if before.mode != after.mode:
        kinds.add("mode")
    return kinds


def commit_changes(trees: TreeReader, commit: CommitObject, parent_trees: list[str]
                   ) -> dict[bytes, tuple[Entry | None, Entry | None]]:
    """The paths *commit* changes (see the module docstring for merges)."""
    if not parent_trees:
        return trees.diff(None, commit.tree, limit=MAX_CHANGED_PATHS)
    if len(parent_trees) == 1:
        return trees.diff(parent_trees[0], commit.tree, limit=MAX_CHANGED_PATHS)
    diffs = [trees.diff(parent, commit.tree, limit=MAX_MERGE_DIFF_PATHS) for parent in parent_trees]
    common = set(diffs[0]).intersection(*diffs[1:])
    return {path: diffs[0][path] for path in sorted(common)}


def _aggregate(union: dict[bytes, PathChange], changes: dict[bytes, tuple[Entry | None, Entry | None]]) -> None:
    """Add one commit's changes to the union (kinds of change, flags, symlink targets, exact renames)."""
    deleted = {(b.sha, b.mode): path for path, (b, a) in changes.items() if a is None and b is not None}
    added = {path: (a.sha, a.mode) for path, (b, a) in changes.items() if b is None and a is not None}
    renamed = {path for path, key in added.items() if key in deleted}
    renamed |= {deleted[added[path]] for path in renamed}
    for path, (before, after) in changes.items():
        item = union.setdefault(path, PathChange())
        item.commits += 1
        item.changes |= _kinds(before, after)
        if after is not None and after.mode == LINK:
            item.flags.add("symlink")
            item.link_targets.add(after.sha)
        if after is not None and after.mode == GITLINK:
            item.flags.add("gitlink")
        if after is not None and after.mode == EXEC and (before is None or before.mode != EXEC):
            item.flags.add("executable")
        if after is not None and after.mode == TREE:     # only an empty subtree is listed (TreeReader.diff)
            item.flags.add("empty-tree")
        if path in renamed:
            item.flags.add("renamed")


def changed_paths(reader: _CommitReader, commits: Iterable[CommitObject], *, source_tree: str, base_tree: str,
                  merge_base_trees: Iterable[str] = ()) -> dict[str, PathChange]:
    """Every path the range base..source changes, aggregated over its commits (oldest last is fine: order does not
    matter), PLUS the net diff from each merge base to the source tip (*merge_base_trees*): a merge commit inside the
    range can take a path unchanged from an OLD base-side commit (e.g. a file the base deleted since), which no
    per-commit rule sees, but which the integration would bring back into the base's tree. *commits* are the verified
    range commits; parents outside them are read (lenient) for their tree. HistoryRefused for a crafted tree or name
    or too many paths; HistoryUnverifiable for an object that cannot be read or verified."""
    trees = TreeReader(reader)
    commits = list(commits)
    known = {commit.sha: commit for commit in commits}   # base-side parents are added as they are read
    union: dict[bytes, PathChange] = {}
    for commit in commits:
        parent_trees = []
        for parent in commit.parents:
            if parent not in known:
                known[parent] = reader.read(parent, strict=False)
            parent_trees.append(known[parent].tree)
        _aggregate(union, commit_changes(trees, commit, parent_trees))
        _check_count(union)
    for merge_base_tree in merge_base_trees:
        _aggregate(union, trees.diff(merge_base_tree, source_tree, limit=MAX_CHANGED_PATHS))
        _check_count(union)
    result: dict[str, PathChange] = {}
    for raw_path, item in union.items():
        problems = [problem for component in raw_path.split(b"/") if (problem := name_problem(component))]
        if problems:
            raise HistoryRefused(f"the source changes a path with a malformed name ({raw_path[:120]!r}): "
                                 f"{problems[0]}")
        path = raw_path.decode("utf-8")
        if len(path) > MAX_PATH_TEXT:
            raise HistoryRefused(f"the source changes a path longer than {MAX_PATH_TEXT} characters ({path[:80]}...)")
        if posixpath.basename(path) in GIT_CONTROL_NAMES:
            item.flags.add("git-control")
        if trees.lookup(source_tree, raw_path) == trees.lookup(base_tree, raw_path):
            item.flags.add("history-only")
        tip = trees.lookup(source_tree, raw_path)
        if tip is not None and tip.mode == LINK:
            item.tip_target = _tip_target_kind(trees, source_tree, path, _link_text(trees, tip.sha))
        item.link_targets = {_link_text(trees, sha) for sha in sorted(item.link_targets)}
        result[path] = item
    return dict(sorted(result.items()))


def _check_count(union: dict[bytes, PathChange]) -> None:
    if len(union) > MAX_CHANGED_PATHS:
        raise HistoryRefused(f"the source changes more than {MAX_CHANGED_PATHS} paths; split it into several jobs "
                             "(a path list is refused, never truncated)")


def _tip_target_kind(trees: TreeReader, source_tree: str, path: str, target: str) -> str:
    """What the target of the symlink *path* (as it is in the source tip) resolves to in the source tip's tree:
    file | tree | link | gitlink | missing | outside (an absolute or escaping target; refused by symlink_problem)."""
    if not target or target.startswith("/") or INVALID_TARGET in target:
        return "outside"
    resolved = posixpath.normpath(posixpath.join(posixpath.dirname(path), target))
    if resolved in (".", "..") or resolved.startswith("../"):
        return "outside"
    entry = trees.lookup(source_tree, resolved.encode("utf-8"))
    return "missing" if entry is None else entry.kind


def _link_text(trees: TreeReader, sha: str) -> str:
    raw = trees.blob(sha, limit=MAX_SYMLINK_TARGET)
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return INVALID_TARGET   # never a valid target: symlink_problem refuses it


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def path_and_ancestors(path: str) -> list[str]:
    """`a/b/c` → [`a`, `a/b`, `a/b/c`]: the path and every directory that contains it."""
    parts = path.split("/")
    return ["/".join(parts[:index]) for index in range(1, len(parts) + 1)]


def matching_prefix(patterns: Iterable[str], path: str) -> str | None:
    """The shortest of *path* and its ancestor directories that matches one of *patterns* (None: none does)."""
    patterns = tuple(patterns)
    return next((prefix for prefix in path_and_ancestors(path)
                 if any(matches(pattern, prefix) for pattern in patterns)), None)


def path_class(policy: PathPolicy, config: ProjectConfig, path: str) -> tuple[str, str | None]:
    """(class, owning role) of a repository path — lexical: protected > approval-required > owned > unowned; the path
    or any of its ancestor directories matching a pattern puts it in that class (see the module docstring)."""
    if matching_prefix(config.protected, path) is not None:
        return "protected", None
    role = policy.owner_of(path)
    if matching_prefix(config.approval_required, path) is not None:
        return "approval-required", role
    return ("owned", role) if role else ("unowned", None)


def _literal_prefix(pattern: str) -> str:
    """The part of a path pattern before its first wildcard."""
    match = re.search(r"[*?\[]", pattern)
    return pattern[:match.start()] if match else pattern


def symlink_problem(config: ProjectConfig, path: str, target: str) -> str | None:
    """Why the symlink *path* → *target* refuses the job (None: it may be authorized like any other path). A target
    that is, or is a directory containing, a protected path is refused (`-> ../backend` would expose
    backend/data/**)."""
    shown = repr(target[:120])
    if not target or INVALID_TARGET in target or re.search(r"[\x00-\x1f\x7f]", target):
        return f"symlink {path} has an empty, non-UTF-8 or control-character target"
    resolved = posixpath.normpath(posixpath.join(posixpath.dirname(path), target))
    inside = [pattern for pattern in config.protected if _literal_prefix(pattern).startswith(f"{resolved}/")]
    problem = None
    if target.startswith("/"):
        problem = f"points to the absolute path {shown}"
    elif resolved in (".", "..") or resolved.startswith("../"):
        problem = f"points outside the repository ({shown})"
    elif any(part.rstrip(". ").lower() == ".git" for part in resolved.split("/")):
        problem = f"points into Git's own directory ({shown})"
    elif any(matching_prefix(config.protected, candidate) is not None for candidate in (resolved, f"{resolved}/x")):
        problem = f"points to the protected path {resolved!r}"
    elif inside:
        problem = f"points to {resolved!r}, which contains the protected path(s) {inside[:3]}"
    return f"symlink {path} {problem}" if problem else None


def tip_target_problem(path: str, kind: str | None) -> str | None:
    """A symlink that stays in the source tip must resolve to a regular file of that tree: a directory could expose
    protected files at any depth (`**/.env`), and a missing target, a chain or a submodule cannot be judged."""
    if kind is None or kind in ("file", "outside"):   # outside: already refused by symlink_problem
        return None
    shown = {"tree": "a directory", "link": "another symlink", "gitlink": "a submodule", "missing": "nothing"}[kind]
    return f"symlink {path} points to {shown} in the source tip; only a symlink to a regular file can be authorized"


@dataclass
class ClassifiedPaths:
    """The job's `paths` entries and what they demand."""

    entries: list[dict[str, Any]]
    refusals: list[str]
    required: list[str]            # paths that still need an explicit per-path authorization
    authorized: list[str]          # paths authorized by the operator's record
    unused: list[str]              # authorized paths the source does not change (reported, harmless)


def classify_paths(changes: dict[str, PathChange], policy: PathPolicy, config: ProjectConfig,
                   approved: Iterable[str]) -> ClassifiedPaths:
    """Classify every changed path and apply the operator's per-path authorizations (*approved*: exact paths)."""
    approved = set(approved)
    entries, refusals, required, authorized = [], [], [], []
    for path, item in changes.items():
        cls, role = path_class(policy, config, path)
        reasons = []
        if cls == "protected":
            through = matching_prefix(config.protected, path)
            reasons.append(f"{path} is protected (never writable, never authorizable)"
                           + (f": it lies inside the protected directory {through}" if through != path else ""))
        if "empty-tree" in item.flags:
            reasons.append(f"{path} is an added empty directory (an empty tree object): Git never records one, so it "
                           "comes from a crafted tree")
        if "gitlink" in item.flags:
            reasons.append(f"{path} adds or changes a submodule (gitlink): its content cannot be inspected")
        for target in sorted(item.link_targets):
            problem = symlink_problem(config, path, target)
            if problem:
                reasons.append(problem)
        problem = tip_target_problem(path, item.tip_target)
        if problem and not reasons:
            reasons.append(problem)
        needs = cls in ("approval-required", "unowned") or bool(item.flags & {"symlink", "git-control"})
        if reasons:
            status = "refused"
            refusals.extend(reasons)
        elif needs and path in approved:
            status = "authorized"
            authorized.append(path)
        elif needs:
            status = "required"
            required.append(path)
        else:
            status = "not-needed"
        entries.append({"path": path, "class": cls, "role": role, "changes": sorted(item.changes),
                        "flags": sorted(item.flags), "authorization": status})
    unused = sorted(approved - set(changes))
    return ClassifiedPaths(entries=entries, refusals=refusals, required=required, authorized=authorized,
                           unused=unused)


def check_authorizable(policy: PathPolicy, config: ProjectConfig, paths: Iterable[str]) -> None:
    """IntegrationError when an authorization record names a protected path, or a path inside a protected directory
    (it can never be authorized)."""
    protected = [path for path in paths if path_class(policy, config, path)[0] == "protected"]
    if protected:
        raise IntegrationError(f"the authorization record names protected path(s) {protected[:10]}: a protected path "
                               "can never be authorized (design §6.2)")
