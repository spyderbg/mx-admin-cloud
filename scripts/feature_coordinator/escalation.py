"""Human escalation: hold a task for a human decision, and release it again (v5 §7.2).

A *human block* is the `needs-human` state. Unlike a dependency block (`blocked`, reason
"waiting for …"), it is never resumed automatically — not when dependencies are verified, not
by a label, a closed Issue or a coordinator restart — only by an explicit release from an
operator: `feature.py release`, or a validated `/feature release` comment (issue_sync.py).

Neither operation touches a running task (stop it first), grants paths, tools or shell
access, or changes any role's permissions: they only move a task between states.
"""

from __future__ import annotations

from .errors import ClaimError, IllegalTransitionError
from .model import Task, TaskState, refresh_readiness, transition_task
from .store import StateStore

HOLDABLE = (TaskState.PROPOSED, TaskState.READY, TaskState.BLOCKED, TaskState.FAILED)


def hold_task(store: StateStore, task_id: str, *, reason: str, actor: str) -> Task:
    """Put *task_id* into `needs-human`. Idempotent for a task already held."""
    with store.transaction():
        task = store.load_task(task_id)
        if task.state is TaskState.NEEDS_HUMAN:
            return task
        if task.state is TaskState.IN_PROGRESS:
            raise ClaimError(f"task {task_id!r} is running; stop it before holding it for a human")
        if task.state not in HOLDABLE:
            raise IllegalTransitionError(f"task {task_id!r} is {task.state.value}; only "
                                         f"{', '.join(s.value for s in HOLDABLE)} tasks can be held")
        transition_task(task, TaskState.NEEDS_HUMAN, f"held for a human decision by {actor}: {reason}")
        store.save_task(task)
        store.append_event("task-held", task=task_id, actor=actor, reason=reason)
    return task


def release_task(store: StateStore, task_id: str, *, actor: str) -> tuple[Task, list[str]]:
    """Release a human hold: `needs-human` → `ready`, then re-check its dependencies (it is
    blocked again at once if they are not verified). Returns the task and changed task IDs."""
    with store.transaction():
        task = store.load_task(task_id)
        if task.state is not TaskState.NEEDS_HUMAN:
            raise IllegalTransitionError(f"task {task_id!r} is {task.state.value}, not held for a human; "
                                         "dependency blocks are released only by verified evidence")
        transition_task(task, TaskState.READY, f"human hold released by {actor}")
        feature = store.load_feature(task.feature_id)
        tasks = store.load_tasks(feature)
        tasks[task_id] = task
        changed = refresh_readiness(tasks)
        for changed_id in {task_id, *changed}:
            store.save_task(tasks[changed_id])
        store.append_event("task-released", task=task_id, actor=actor, state=tasks[task_id].state.value)
    return tasks[task_id], changed
