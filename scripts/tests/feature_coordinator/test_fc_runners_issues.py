"""Mock Claude Code / Codex runners, the OpenAI-compatible Qwen runner (mocked) and the issue tracker."""

# pylint: disable=missing-function-docstring,redefined-outer-name

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from feature_coordinator.config import ProjectConfig
from feature_coordinator.errors import ProviderAuthError, ProviderUnavailableError, PullRequestPolicyError, TrackerError
from feature_coordinator.issues import BLOCKED_LABEL, MockIssueTracker
from feature_coordinator.paths import PathPolicy, ScopedWorkspace
from feature_coordinator.providers import REMOTE_QWEN
from feature_coordinator.runners import (
    FileEdit,
    MockChatTransport,
    MockClaudeCodeRunner,
    MockCodexRunner,
    OpenAICompatibleRunner,
    RunRequest,
    chat_response,
)

LOOPBACK = {"AGENT_QWEN_BASE_URL": "http://127.0.0.1:8080/v1", "AGENT_QWEN_API_KEY": "sk-test-secret"}
VALIDATED = dataclasses.replace(REMOTE_QWEN, validated_capabilities=frozenset({"tool_calling"}))


@pytest.fixture
def policy(repo: Path, config: ProjectConfig) -> PathPolicy:
    return PathPolicy(repo, config)


def req(policy: PathPolicy, role: str = "automation", task_id: str = "f.t") -> RunRequest:
    return RunRequest(task_id, role, "do it", ScopedWorkspace(policy, role))


def qwen(transport: MockChatTransport, profile=REMOTE_QWEN, env=None) -> OpenAICompatibleRunner:
    return OpenAICompatibleRunner(profile, LOOPBACK if env is None else env, transport, sleep=lambda _s: None)


# -- mock CLI runners -----------------------------------------------------------

def test_mock_claude_code_runner_writes_within_scope(policy: PathPolicy, repo: Path, config: ProjectConfig) -> None:
    runner = MockClaudeCodeRunner(config.providers["claude"], {"f.t": [FileEdit("backend/src/app.py", "X = 1\n")]})
    result = runner.run(req(policy, "backend"))
    assert result.status == "completed" and result.changed_paths == ["backend/src/app.py"]
    assert (repo / "backend/src/app.py").read_text(encoding="utf-8") == "X = 1\n"


def test_mock_codex_runner_out_of_scope_write_is_rejected(policy: PathPolicy, repo: Path,
                                                          config: ProjectConfig) -> None:
    runner = MockCodexRunner(config.providers["codex"], {"f.t": [FileEdit("backend/src/app.py", "hacked")]})
    result = runner.run(req(policy, "frontend"))
    assert result.status == "rejected" and "owned by role 'backend'" in result.error
    assert (repo / "backend/src/app.py").read_text(encoding="utf-8") == "ROUTES = []\n"


def test_planned_cli_invocations_avoid_bypass_modes(policy: PathPolicy, config: ProjectConfig) -> None:
    claude = MockClaudeCodeRunner(config.providers["claude"]).planned_invocation(req(policy, "backend"))
    assert claude[:2] == ["claude", "-p"] and "user,local" in claude
    assert "bypassPermissions" not in claude and "--dangerously-skip-permissions" not in claude
    codex = MockCodexRunner(config.providers["codex"]).planned_invocation(req(policy, "frontend"))
    assert codex[:2] == ["codex", "exec"] and "workspace-write" in codex
    assert not any("dangerously" in arg for arg in codex)


# -- remote Qwen (OpenAI-compatible) ---------------------------------------------

def test_default_transport_never_connects(policy: PathPolicy) -> None:
    runner = OpenAICompatibleRunner(REMOTE_QWEN, LOOPBACK)
    result = runner.run(req(policy))
    assert result.status == "rejected" and "live model connections are disabled" in result.error


def test_unconfigured_endpoint_marks_the_role_unavailable(policy: PathPolicy) -> None:
    transport = MockChatTransport([chat_response("x")])
    result = qwen(transport, env={}).run(req(policy))
    assert result.status == "unavailable" and "AGENT_QWEN_BASE_URL" in result.error
    assert not transport.requests


def test_plain_http_remote_endpoint_is_blocked_before_any_request(policy: PathPolicy) -> None:
    transport = MockChatTransport([chat_response("x")])
    result = qwen(transport, env={"AGENT_QWEN_BASE_URL": "http://10.20.30.40:8080/v1"}).run(req(policy))
    assert result.status == "rejected" and "D-15" in result.error and not transport.requests


def test_request_shape_matches_the_exact_profile(policy: PathPolicy) -> None:
    transport = MockChatTransport([chat_response("reviewed")])
    result = qwen(transport).run(req(policy))
    assert result.status == "advisory"  # tool calling not validated → read-only
    sent = transport.requests[0]
    assert sent.url == "http://127.0.0.1:8080/v1/chat/completions"
    assert sent.payload["model"] == "Qwen3.6-35B-A3B-Q4_K_M.gguf"
    assert sent.payload["temperature"] == 0.2 and sent.payload["stream"] is False
    assert 0 < sent.payload["max_tokens"] <= 16384 and "tools" not in sent.payload
    assert (sent.connect_timeout, sent.request_timeout) == (10.0, 180.0)
    assert sent.has_api_key and "sk-test-secret" not in str(sent.payload)


def test_unvalidated_tool_calls_are_refused(policy: PathPolicy, repo: Path) -> None:
    transport = MockChatTransport([chat_response(tool_calls=[{"name": "find-replace", "arguments": {
        "path": "scripts/e2e_check.py", "find": "ok", "replace": "changed"}}])])
    result = qwen(transport).run(req(policy))
    assert result.status == "rejected" and result.rejected_tool_calls == ["find-replace"]
    assert (repo / "scripts/e2e_check.py").read_text(encoding="utf-8") == "print('ok')\n"


def test_validated_tool_calls_edit_locally_within_scope(policy: PathPolicy, repo: Path) -> None:
    transport = MockChatTransport([
        chat_response(tool_calls=[
            {"id": "a", "name": "find-replace", "arguments": {"path": "scripts/e2e_check.py", "find": "'ok'",
                                                                "replace": "'all good'"}},
            {"id": "b", "name": "find-replace", "arguments": {"path": "backend/src/app.py", "find": "[]",
                                                                "replace": "['x']"}},
            {"id": "c", "name": "apply-patch", "arguments": {"path": "scripts/e2e_check.py", "patch": "..."}},
            {"id": "d", "name": "multi-find-replace", "arguments": {"path": "scripts/e2e_check.py", "edits": [
                {"find": "good", "replace": "fine"}, {"find": "missing", "replace": "x"}]}},
        ]),
        chat_response("done"),
    ])
    result = qwen(transport, VALIDATED).run(req(policy))
    assert result.status == "completed" and result.changed_paths == ["scripts/e2e_check.py"]
    assert (repo / "scripts/e2e_check.py").read_text(encoding="utf-8") == "print('all good')\n"  # multi edit atomic
    assert (repo / "backend/src/app.py").read_text(encoding="utf-8") == "ROUTES = []\n"
    reasons = "\n".join(result.rejected_tool_calls)
    assert "owned by role 'backend'" in reasons and "not implemented" in reasons and "exactly once" in reasons
    tool_messages = [m for m in transport.requests[1].payload["messages"] if m["role"] == "tool"]
    assert len(tool_messages) == 4 and tool_messages[0]["content"].startswith("ok")
    assert {t["function"]["name"] for t in transport.requests[0].payload["tools"]} == {"find-replace",
                                                                                      "multi-find-replace"}


def test_bounded_retries_then_success(policy: PathPolicy) -> None:
    sleeps: list[float] = []
    transport = MockChatTransport([ProviderUnavailableError("down"), ProviderUnavailableError("down"),
                                   chat_response("ok")])
    runner = OpenAICompatibleRunner(REMOTE_QWEN, LOOPBACK, transport, sleep=sleeps.append)
    assert runner.run(req(policy)).status == "advisory"
    assert sleeps == [0.5, 1.0]


def test_retries_are_bounded_and_unavailability_is_reported(policy: PathPolicy) -> None:
    transport = MockChatTransport([ProviderUnavailableError("timeout")] * 5)
    result = qwen(transport).run(req(policy))
    assert result.status == "unavailable" and len(transport.requests) == 3  # 1 + max_retries(2)


def test_auth_errors_are_not_retried_and_keys_are_redacted(policy: PathPolicy) -> None:
    transport = MockChatTransport([ProviderAuthError("401 for key sk-test-secret"), chat_response("x")])
    result = qwen(transport).run(req(policy))
    assert result.status == "failed" and len(transport.requests) == 1
    assert "sk-test-secret" not in result.error and "***" in result.error


@pytest.mark.parametrize("response,match", [
    ({"choices": []}, "choices"),
    ({"model": "other-model", "choices": [{"message": {"content": "x"}}]}, "model mismatch"),
])
def test_invalid_responses_fail(policy: PathPolicy, response: dict, match: str) -> None:
    result = qwen(MockChatTransport([response])).run(req(policy))
    assert result.status == "failed" and match in result.error


def test_context_budget_is_enforced_before_sending(policy: PathPolicy) -> None:
    transport = MockChatTransport([chat_response("x")])
    request = req(policy)
    request.prompt = "x" * (3 * 110_592)
    result = qwen(transport).run(request)
    assert result.status == "failed" and "working cap" in result.error and not transport.requests


# -- issue tracker -----------------------------------------------------------------

def test_issues_are_deduplicated_and_namespaced(tmp_path: Path) -> None:
    tracker = MockIssueTracker(tmp_path / "tracker.json")
    first = tracker.ensure_issue("task:f.api", "API", "body", ["feature:backend"])
    assert tracker.ensure_issue("task:f.api", "API again", "other", ["feature:backend"]).number == first.number
    with pytest.raises(TrackerError, match="namespace"):
        tracker.ensure_issue("task:x", "X", "b", ["bug"])
    reloaded = MockIssueTracker(tmp_path / "tracker.json")  # survives a coordinator restart
    assert reloaded.ensure_issue("task:f.api", "API", "body", ["feature:backend"]).number == first.number


def test_bug_workflow_issues_are_never_touched() -> None:
    tracker = MockIssueTracker()
    foreign = tracker.add_foreign_issue("flaky test", ["mx-auto", "severity::high"])
    for action in (lambda: tracker.set_labels(foreign.number, add=[BLOCKED_LABEL]),
                   lambda: tracker.comment(foreign.number, "hi"), lambda: tracker.close(foreign.number)):
        with pytest.raises(TrackerError, match="independent bug workflow"):
            action()
    assert tracker.find_by_key("anything") is None


def test_one_pull_request_per_feature_branch() -> None:
    tracker = MockIssueTracker()
    tracker.create_pull_request("feature/f", "dev", "F", "body")
    with pytest.raises(PullRequestPolicyError, match="single PR"):
        tracker.create_pull_request("feature/f", "dev", "F again", "body")
    with pytest.raises(PullRequestPolicyError, match="only feature branches"):
        tracker.create_pull_request("ai/42", "dev", "bug fix", "body")
