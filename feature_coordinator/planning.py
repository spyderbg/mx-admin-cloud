"""W4.5: the execution plan of a GitHub Issue — schema, deterministic validation, record and rendering.

The AI Feature Planner (planner.py) proposes a plan as JSON; nothing it says is trusted. This
module turns a proposal into the CANONICAL plan, or refuses it, by deterministic rules only:

  * the schema: exactly the documented keys (`grants`, `steps`, `provides_contract` or any other
    key is refused — a plan can never widen a permission), string and list bounds, no control
    characters;
  * tasks: 1..`github.planning.max_tasks`, unique IDs (`[a-z][a-z0-9-]{0,31}`), at most ONE task
    per role (no task splitting for its own sake);
  * roles: enabled, and with intake gates (`github.intake.role_gates`): the same roles an operator
    could approve by hand. Anything else — a disabled role, `automation`, an unknown role — is
    refused; if the work cannot be assigned within the role policies the planner must say so
    (`attention`) and an operator decides;
  * paths (what the task intends to change; guidance for the agent and the reviewer — the role
    policy still decides every write at run time): an exact repository path or `<dir>/**`,
    inside the role's owned paths, outside its exclusions, never protected and never
    approval-required (those need a task grant, which a plan cannot carry);
  * dependencies: known task IDs, no self-dependency, no cycle (and the feature DAG is validated
    again by workflow.plan);
  * gates: every role's intake gates are REQUIRED and always present, first and in their configured
    order (as for `/worker approve`); a plan may add configured gates (after them, in configuration
    order), never remove one. The integration gates are the
    coordinator's (workflow.DEFAULT_INTEGRATION_GATES).

The canonical plan (`PLAN_SCHEMA`, plan_digest.py) carries the repository, the Issue identity,
the Issue's spec digest at planning time, the plan VERSION, the feature and the tasks; its
SHA-256 is `plan_sha256`. Plans are versioned per Issue: a revised plan is a new version, and an
approval binds exactly one version (plan_digest.approval_statement).

The plan record (state store kind `plan`, plan/issue-<N>.json) keeps every version of an Issue's
plan, the request each answered, its publication, notices already posted and the approval.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Sequence

from .config import ProjectConfig, matches
from .errors import StateDirError
from .issues import escape_markdown_comment
from .model import utc_now
from .plan_digest import (
    APPROVAL_FORMAT,
    LABEL_APPROVE,
    LABEL_PLAN,
    MAX_MARKER_JSON,
    PLAN_MARKER,
    PLAN_SCHEMA,
    canonical_json,
    encode_marker,
    plan_sha256,
)

PLAN_RECORD_SCHEMA = 1
TASK_ID_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")      # newline and tab allowed in long text
_LINE_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")            # nothing allowed in one-line text
_PATH_SEGMENT_RE = re.compile(r"^[A-Za-z0-9._@+-]+$")
MAX_TITLE = 200
MAX_SUMMARY = 2000
MAX_CRITERION = 500
MAX_CRITERIA = 12
MAX_PATHS = 20
PROPOSAL_KEYS = frozenset({"feature", "tasks", "attention"})
FEATURE_KEYS = frozenset({"title", "summary", "acceptance_criteria"})
TASK_KEYS = frozenset({"id", "title", "role", "instructions", "acceptance_criteria", "paths", "depends_on", "gates"})
INTEGRATION_GATES = ("contract-check", "docs-check")  # = workflow.DEFAULT_INTEGRATION_GATES (a test enforces it)


# ---------------------------------------------------------------------------
# What the planner may use
# ---------------------------------------------------------------------------

def available_roles(config: ProjectConfig) -> dict[str, dict[str, Any]]:
    """Roles a plan may assign: enabled and with intake gates (the hand-approvable roles)."""
    gates = config.github.intake.role_gates
    return {name: {"owned": list(role.owned), "excluded": list(role.excluded), "gates": list(gates[name])}
            for name, role in config.roles.items() if role.enabled and gates.get(name)}


def proposal_schema(config: ProjectConfig) -> dict[str, Any]:
    """The JSON Schema given to the planner (Claude Code `--json-schema`). The deterministic
    validation below is the authority; this only shapes the model's answer."""
    text = {"type": "string"}
    texts = {"type": "array", "items": text}
    return {
        "type": "object", "additionalProperties": False, "required": ["feature", "tasks", "attention"],
        "properties": {
            "feature": {"type": "object", "additionalProperties": False,
                        "required": ["title", "summary", "acceptance_criteria"],
                        "properties": {"title": text, "summary": text, "acceptance_criteria": texts}},
            "tasks": {"type": "array", "items": {
                "type": "object", "additionalProperties": False, "required": sorted(TASK_KEYS),
                "properties": {"id": text, "title": text, "role": {"type": "string",
                                                                   "enum": sorted(available_roles(config))},
                               "instructions": text, "acceptance_criteria": texts, "paths": texts,
                               "depends_on": texts, "gates": texts}}},
            "attention": {"type": ["string", "null"]},
        },
    }


# ---------------------------------------------------------------------------
# Deterministic validation
# ---------------------------------------------------------------------------

class _Problems(list):  # type: ignore[type-arg]
    """Collected validation problems (strings)."""

    def text(self, where: str, value: Any, limit: int, *, multiline: bool = False, required: bool = True) -> str:
        """A bounded string, or "" after recording why not."""
        if not isinstance(value, str):
            self.append(f"{where} must be a string")
            return ""
        value = value.strip()
        if required and not value:
            self.append(f"{where} must not be empty")
        if len(value) > limit:
            self.append(f"{where} has {len(value)} characters; at most {limit} are allowed")
        if (_CONTROL_RE if multiline else _LINE_CONTROL_RE).search(value):
            self.append(f"{where} contains control characters")
        return value

    def texts(self, where: str, value: Any, limit: int, *, minimum: int, maximum: int) -> list[str]:
        """A bounded list of bounded one-line strings."""
        if not isinstance(value, list):
            self.append(f"{where} must be a list")
            return []
        if not minimum <= len(value) <= maximum:
            self.append(f"{where} must have {minimum}..{maximum} entries, has {len(value)}")
        return [self.text(f"{where}[{i}]", item, limit) for i, item in enumerate(value)]

    def keys(self, where: str, value: Any, allowed: frozenset[str], optional: frozenset[str] = frozenset()) -> bool:
        """True when *value* is an object with only *allowed* keys, all present but *optional* (else recorded)."""
        if not isinstance(value, dict):
            self.append(f"{where} must be an object")
            return False
        extra, missing = sorted(set(value) - allowed), sorted(allowed - optional - set(value))
        if extra:
            self.append(f"{where} has keys that are not allowed: {extra} (a plan cannot carry grants, steps, "
                        "contracts or any other permission)")
        if missing:
            self.append(f"{where} is missing keys: {missing}")
        return not extra and not missing


def path_problem(config: ProjectConfig, role: str, path: str) -> str | None:  # pylint: disable=too-many-return-statements
    """Why *path* (exact, or `<dir>/**`) is not a path the role may plan to change (None: it may)."""
    if not path or path.startswith("/") or "\\" in path or _LINE_CONTROL_RE.search(path):
        return "is not a relative POSIX path"
    subtree = path.endswith("/**")
    body = path[:-3] if subtree else path
    segments = body.split("/")
    if any(seg in ("", ".", "..") or not _PATH_SEGMENT_RE.fullmatch(seg) for seg in segments):
        return "must be an exact path or `<dir>/**` (no `..`, no wildcards)"
    probe = f"{body}/__any__" if subtree else body
    for pattern in config.protected:
        if matches(pattern, probe) or matches(pattern, body):
            return f"is protected ({pattern}); no task may change it"
    for pattern in config.approval_required:
        if matches(pattern, probe) or matches(pattern, body):
            return f"is approval-required ({pattern}); a plan cannot grant it"
    spec = config.role(role)
    if not any(matches(pattern, probe) for pattern in spec.owned):
        return f"is outside the {role} role's owned paths {list(spec.owned)}"
    if any(matches(pattern, probe) or matches(pattern, body) for pattern in spec.excluded):
        return f"is excluded from the {role} role"
    return None


def _cycle(edges: Mapping[str, Sequence[str]]) -> list[str] | None:
    """One dependency cycle (as a path), or None."""
    state: dict[str, int] = {}

    def visit(node: str, trail: list[str]) -> list[str] | None:
        state[node] = 1
        for dep in edges.get(node, ()):
            if state.get(dep) == 1:
                return [*trail[trail.index(dep):], dep] if dep in trail else [node, dep]
            if dep in edges and state.get(dep) is None:
                found = visit(dep, [*trail, dep])
                if found:
                    return found
        state[node] = 2
        return None

    for start in edges:
        if state.get(start) is None:
            found = visit(start, [start])
            if found:
                return found
    return None


@dataclass(frozen=True)
class PlanOutcome:
    """The result of validating one proposal."""

    plan: dict[str, Any] | None              # the canonical plan, or None
    problems: tuple[str, ...] = ()           # why the proposal was refused (fed back to the planner)
    attention: str = ""                      # the planner itself asked for an operator


def _gates_in_order(config: ProjectConfig, gates: Sequence[str]) -> list[str]:
    wanted = set(gates)
    return [g for g in config.gate_names if g in wanted]


def canonical_plan(proposal: Any, *, config: ProjectConfig, repository: str, issue: Mapping[str, Any],  # pylint: disable=too-many-locals,too-many-branches,too-many-statements
                   spec_sha: str, version: int) -> PlanOutcome:
    """Validate *proposal* (the planner's JSON) and build the canonical plan (see the module docstring).
    *issue*: {number, id, node_id}."""
    problems = _Problems()
    if not problems.keys("plan", proposal, PROPOSAL_KEYS, frozenset({"attention"})):
        if isinstance(proposal, dict) and isinstance(proposal.get("attention"), str) and proposal["attention"].strip():
            return PlanOutcome(None, (), problems.text("attention", proposal["attention"], 1000, multiline=True))
        return PlanOutcome(None, tuple(problems))
    attention = proposal.get("attention")
    if attention is not None:
        reason = problems.text("attention", attention, 1000, multiline=True, required=False)
        if reason:
            return PlanOutcome(None, (), reason)
    planning = config.github.planning
    roles = available_roles(config)
    feature: dict[str, Any] = {}
    if problems.keys("feature", proposal["feature"], FEATURE_KEYS):
        raw = proposal["feature"]
        feature = {"title": problems.text("feature.title", raw["title"], MAX_TITLE),
                   "summary": problems.text("feature.summary", raw["summary"], MAX_SUMMARY, multiline=True,
                                            required=False),
                   "acceptance_criteria": problems.texts("feature.acceptance_criteria", raw["acceptance_criteria"],
                                                         MAX_CRITERION, minimum=1, maximum=MAX_CRITERIA)}
    raw_tasks = proposal["tasks"]
    tasks: list[dict[str, Any]] = []
    if not isinstance(raw_tasks, list) or not 1 <= len(raw_tasks) <= planning.max_tasks:
        problems.append(f"tasks must be a list of 1..{planning.max_tasks} tasks")
        raw_tasks = raw_tasks if isinstance(raw_tasks, list) else []
    seen_roles: dict[str, str] = {}
    for index, raw in enumerate(raw_tasks):
        where = f"tasks[{index}]"
        if not problems.keys(where, raw, TASK_KEYS):
            continue
        task_id = raw["id"] if isinstance(raw["id"], str) else ""
        if not TASK_ID_RE.fullmatch(task_id):
            problems.append(f"{where}.id {task_id!r} must match {TASK_ID_RE.pattern}")
        where = f"task {task_id!r}"
        role = raw["role"] if isinstance(raw["role"], str) else ""
        if role not in roles:
            why = ("is disabled" if role in config.roles and not config.role(role).enabled else
                   "has no intake gates (github.intake.role_gates)" if role in config.roles else "is unknown")
            problems.append(f"{where}: role {role!r} {why}; available roles: {sorted(roles)}")
        elif role in seen_roles:
            problems.append(f"{where}: role {role!r} already has task {seen_roles[role]!r}; one task per role "
                            "(merge them)")
        else:
            seen_roles[role] = task_id
        paths = problems.texts(f"{where}.paths", raw["paths"], 300, minimum=1, maximum=MAX_PATHS)
        if role in roles:
            for path in paths:
                why = path_problem(config, role, path)
                if why:
                    problems.append(f"{where}: path {path!r} {why}")
        gates = raw["gates"] if isinstance(raw["gates"], list) else []
        if not isinstance(raw["gates"], list):
            problems.append(f"{where}.gates must be a list")
        unknown = [g for g in gates if not isinstance(g, str) or g not in config.gate_names]
        if unknown:
            problems.append(f"{where}: unknown gates {unknown}")
        required = roles.get(role, {}).get("gates", [])
        depends = raw["depends_on"] if isinstance(raw["depends_on"], list) else []
        if not isinstance(raw["depends_on"], list) or not all(isinstance(d, str) for d in depends):
            problems.append(f"{where}.depends_on must be a list of task IDs")
            depends = [d for d in depends if isinstance(d, str)]
        tasks.append({
            "id": task_id, "role": role,
            "title": problems.text(f"{where}.title", raw["title"], MAX_TITLE),
            "instructions": problems.text(f"{where}.instructions", raw["instructions"],
                                          planning.max_instructions_characters, multiline=True),
            "acceptance_criteria": problems.texts(f"{where}.acceptance_criteria", raw["acceptance_criteria"],
                                                  MAX_CRITERION, minimum=1, maximum=MAX_CRITERIA),
            "paths": sorted(dict.fromkeys(paths)),
            "depends_on": sorted(dict.fromkeys(depends)),
            "gates": [*required, *(g for g in _gates_in_order(config, [g for g in gates if isinstance(g, str)])
                                   if g not in required)],
        })
    ids = [t["id"] for t in tasks]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        problems.append(f"task IDs must be unique: {duplicates}")
    for task in tasks:
        unknown = [d for d in task["depends_on"] if d not in ids]
        if unknown:
            problems.append(f"task {task['id']!r} depends on unknown tasks {unknown}")
        if task["id"] in task["depends_on"]:
            problems.append(f"task {task['id']!r} depends on itself")
    cycle = _cycle({t["id"]: [d for d in t["depends_on"] if d != t["id"]] for t in tasks})
    if cycle:
        problems.append(f"the task dependencies form a cycle: {' -> '.join(cycle)}")
    if problems:
        return PlanOutcome(None, tuple(dict.fromkeys(problems)))
    plan = {
        "schema": PLAN_SCHEMA, "version": int(version), "repository": repository.lower(),
        "issue": {"number": int(issue["number"]), "id": int(issue["id"]), "node_id": str(issue["node_id"])},
        "spec_sha256": spec_sha,
        "feature": {"id": f"issue-{int(issue['number'])}", "base_branch": config.base_branch, **feature},
        "tasks": tasks,
        "integration_gates": _gates_in_order(config, INTEGRATION_GATES),
    }
    size = len(canonical_json(plan))
    if size > MAX_MARKER_JSON:
        return PlanOutcome(None, (f"the plan is too large ({size} bytes of canonical JSON; at most "
                                  f"{MAX_MARKER_JSON}): shorten the instructions",))
    return PlanOutcome(plan)


def plan_problems(plan: Mapping[str, Any], config: ProjectConfig) -> list[str]:
    """Re-validate a stored canonical plan against the CURRENT configuration (before it is approved
    and planned: a role disabled since, or a gate removed, refuses it)."""
    proposal = {"feature": {k: plan["feature"][k] for k in FEATURE_KEYS},
                "tasks": [{k: t[k] for k in TASK_KEYS} for t in plan["tasks"]], "attention": None}
    outcome = canonical_plan(proposal, config=config, repository=plan["repository"], issue=plan["issue"],
                             spec_sha=plan["spec_sha256"], version=plan["version"])
    if outcome.plan is None:
        return list(outcome.problems) or [f"the planner asked for attention: {outcome.attention}"]
    if outcome.plan != dict(plan):
        return ["the stored plan differs from its re-validation under the current configuration (gates or role "
                "policies changed since it was planned): request a revised plan"]
    return []


# ---------------------------------------------------------------------------
# The plan record
# ---------------------------------------------------------------------------

PLAN_STATUSES = ("idle", "planning", "published", "attention", "stale", "approved")
_RECORD_FIELDS = frozenset({"schema_version", "id", "issue_number", "issue_id", "node_id", "url", "status",
                            "request", "handled_requests", "attempts", "feedback", "versions", "attention",
                            "approval", "rejected", "notices", "updated_at", "revision"})


@dataclass
class PlanRecord:  # pylint: disable=too-many-instance-attributes
    """Every plan version of one Issue and what happened to it (kind `plan`, id `issue-<N>`)."""

    id: str
    issue_number: int
    issue_id: int
    node_id: str
    url: str = ""
    status: str = "idle"       # idle (bookkeeping) → planning → published | attention; published → stale | approved
    request: dict[str, Any] | None = None              # the `worker:plan` label event being answered
    handled_requests: list[int] = field(default_factory=list)  # label event IDs already answered
    attempts: int = 0                                  # planner invocations for `request`
    feedback: list[str] = field(default_factory=list)  # problems of the last refused proposal (fed back)
    versions: list[dict[str, Any]] = field(default_factory=list)
    attention: dict[str, Any] | None = None            # {reason, at, request}
    approval: dict[str, Any] | None = None             # the validated approval evidence
    rejected: list[int] = field(default_factory=list)  # receipt comment / label event IDs refused (never re-read)
    notices: list[str] = field(default_factory=list)   # op names of notices already posted
    updated_at: str = field(default_factory=utc_now)
    revision: int = 0

    def to_dict(self) -> dict[str, Any]:
        """JSON form."""
        return {**asdict(self), "schema_version": PLAN_RECORD_SCHEMA}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PlanRecord":
        """Strict: another schema, unknown fields or an unknown status are refused."""
        if data.get("schema_version") != PLAN_RECORD_SCHEMA:
            raise StateDirError(f"plan record {data.get('id')!r}: unsupported schema {data.get('schema_version')!r}")
        unknown = sorted(set(data) - _RECORD_FIELDS)
        if unknown:
            raise StateDirError(f"plan record {data.get('id')!r} has unknown fields {unknown}")
        record = cls(**{k: v for k, v in data.items() if k != "schema_version"})
        if record.status not in PLAN_STATUSES:
            raise StateDirError(f"plan record {record.id!r}: unknown status {record.status!r}")
        return record

    @property
    def latest(self) -> dict[str, Any] | None:
        """The newest plan version (None before the first)."""
        return self.versions[-1] if self.versions else None


def load_plan(store: Any, feature_id: str) -> PlanRecord | None:
    """The plan record of an Issue's feature ID (None: never planned)."""
    raw = store.load_record("plan", feature_id)
    return PlanRecord.from_dict(raw) if raw else None


# ---------------------------------------------------------------------------
# Rendering (the plan comment) and the approved feature
# ---------------------------------------------------------------------------

_MENTION_RE = re.compile(r"@(?=[A-Za-z0-9])")


def _md(text: str) -> str:
    """Planner or Issue text made inert in a comment: no HTML comment, no @mention."""
    return _MENTION_RE.sub("@\u200b", escape_markdown_comment(text))


def _cell(text: str) -> str:
    return _md(text).replace("|", "\\|").replace("\n", " ")


def render_plan(plan: Mapping[str, Any], *, sha: str, request: Mapping[str, Any] | None, op: str) -> str:
    """The plan comment: readable plan, review instructions, the marker carrying the canonical plan."""
    version, number = plan["version"], plan["issue"]["number"]
    feature = plan["feature"]
    lines = [op, f"## Execution plan v{version} for #{number}", "",
             f"**{_md(feature['title'])}**", ""]
    if feature["summary"]:
        lines += [_md(feature["summary"]), ""]
    lines += ["### Acceptance criteria", *(f"- {_md(c)}" for c in feature["acceptance_criteria"]), "",
              f"### Tasks ({len(plan['tasks'])})", "", "| Task | Role | Depends on | Gates |", "|---|---|---|---|"]
    for task in plan["tasks"]:
        deps = ", ".join(f"`{d}`" for d in task["depends_on"]) or "—"
        gates = ", ".join(f"`{g}`" for g in task["gates"])
        lines.append(f"| `{task['id']}` — {_cell(task['title'])} | {task['role']} | {deps} | {gates} |")
    lines.append("")
    for task in plan["tasks"]:
        lines += [f"<details><summary>Task <code>{task['id']}</code> ({task['role']}): instructions</summary>", "",
                  _md(task["instructions"]), "", "Acceptance criteria:",
                  *(f"- {_md(c)}" for c in task["acceptance_criteria"]), "",
                  "Intended paths (the role policy still decides every write): "
                  + ", ".join(f"`{p}`" for p in task["paths"]), "", "</details>", ""]
    lines += [f"Integration gates: {', '.join(f'`{g}`' for g in plan['integration_gates'])}", "",
              "### Review", "",
              f"- **Approve** exactly this Issue text and plan v{version}: add the label `{LABEL_APPROVE}`. Nothing "
              "has to be copied; the approval binds the Issue's title and body, this plan version and its roles, "
              "dependencies and gates.",
              f"- **Revise**: remove and re-add `{LABEL_PLAN}` (edit the Issue first if the request changes). A "
              "revised plan is a new version; an approval of an earlier version is refused.",
              "- Editing the Issue's title or body makes this plan stale: approve only after a fresh plan.",
              "- Nothing runs before the coordinator validated an approval.", "",
              f"<sub>Plan sha256 `{sha}` · Issue spec sha256 `{plan['spec_sha256'][:16]}…` · approval format "
              f"`{APPROVAL_FORMAT}`"
              + (f" · requested by `{request.get('actor')}` (label event {request.get('id')})" if request else "")
              + "</sub>", "", encode_marker(PLAN_MARKER, plan)]
    return "\n".join(lines)


def task_instructions(plan: Mapping[str, Any], task: Mapping[str, Any], *, approval_sha: str, approved_by: str,
                      title: str, body: str) -> str:
    """The acceptance text of one planned task (its prompt, workflow._task_prompt)."""
    number = plan["issue"]["number"]
    others = [f"`{t['id']}` ({t['role']})" for t in plan["tasks"] if t["id"] != task["id"]]
    return (f"Implement task `{task['id']}` of the execution plan v{plan['version']} for GitHub Issue #{number}, "
            f"approved by {approved_by} (approval sha256 {approval_sha[:16]}). Work only within this role's paths"
            + (f"; the other tasks of this feature ({', '.join(others)}) are done by other roles" if others else "")
            + ".\n\nTask instructions:\n" + task["instructions"]
            + "\n\nTask acceptance criteria:\n" + "\n".join(f"- {c}" for c in task["acceptance_criteria"])
            + "\n\nIntended paths: " + ", ".join(task["paths"])
            + "\n\nFeature acceptance criteria:\n" + "\n".join(f"- {c}" for c in plan["feature"]["acceptance_criteria"])
            + f"\n\nThe approved Issue specification follows for context:\n\n# {title}\n\n{body}")


def plan_digest_of(plan: Mapping[str, Any]) -> str:
    """plan_digest.plan_sha256 (re-exported for callers of this module)."""
    return plan_sha256(plan)
