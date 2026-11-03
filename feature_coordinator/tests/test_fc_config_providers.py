"""Project configuration ceilings and the remote OpenAI-compatible Qwen profile."""

# pylint: disable=missing-function-docstring

from __future__ import annotations

import copy
import dataclasses
import re
from pathlib import Path

import pytest
import yaml

from feature_coordinator.config import ProjectConfig, RoleConfig, config_from_mapping, default_config, load_config
from feature_coordinator.errors import (
    ConfigError,
    ContextBudgetError,
    ProviderError,
    ProviderUnavailableError,
    TransportPolicyError,
)
from feature_coordinator.providers import (
    REMOTE_QWEN,
    chat_completions_url,
    check_budget,
    check_transport,
    redact,
    resolve_endpoint,
)

from fc_helpers import SCRIPTS_DIR, pre_relocation_example

EXAMPLE = Path(__file__).resolve().parents[2] / "docs_agents" / "coordination" / "project-config.example.yaml"


def example_data() -> dict:
    return yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))


def test_documented_example_equals_the_built_in_configuration() -> None:
    assert load_config(EXAMPLE) == default_config()


def test_default_role_providers_follow_v5() -> None:
    cfg = default_config()
    assert {r: cfg.role(r).provider for r in cfg.roles} == {
        "backend": "claude", "cli": "claude", "frontend": "codex", "automation": "remote_qwen",
        "coordination": "claude"}


@pytest.mark.parametrize("mutate,match", [
    (lambda d: d["roles"]["backend"].update(max_writers=2), "max_writers"),
    (lambda d: d["git"].update(single_mutation_coordinator=False), "ceiling"),
    (lambda d: d["git"].update(no_per_agent_worktrees=False), "ceiling"),
    (lambda d: d.update(execution_host="multi-host"), "single-linux"),
    (lambda d: d["feature"].update(pr_policy="per-task"), "one-after-all-gates"),
    (lambda d: d["roles"]["cli"].update(owned_paths=["cli/**", "backend/src/**"]), "overlapping"),
    (lambda d: d["roles"]["cli"].update(owned_paths=["../cli/**"]), "segments"),
    (lambda d: d["roles"]["cli"].update(owned_paths=["cli/*.py"]), "only be"),
    (lambda d: d["providers"]["remote_qwen"].update(base_url="http://example.invalid/v1"), "environment only"),
    (lambda d: d["providers"]["remote_qwen"].update(effective_max_input_tokens=128000), "exceeds"),
    (lambda d: d["state"].update(allowed_filesystems=["ext4", "fuseblk"]), "FUSE"),
])
def test_relaxing_a_safety_ceiling_is_rejected(mutate, match: str) -> None:
    data = copy.deepcopy(example_data())
    mutate(data)
    with pytest.raises(ConfigError, match=match):
        config_from_mapping(data)


LOCAL_TOML, TOML = "test_environment/testenv.local.toml", "test_environment/testenv.toml"


@pytest.mark.parametrize("mutate,match", [
    (lambda d: d.update(yaml.safe_load(pre_relocation_example())), "pre-relocation test environment path(s) "
     "['scripts/mxfix/**', 'scripts/mxissues/**', 'testenv.toml']"),
    (lambda d: d["protected_paths"].append("scripts/mxfix/**"), "pre-relocation"),  # old beside new: unmigrated
    (lambda d: d["roles"]["automation"]["excluded_paths"].append("scripts/mxissues/**"), "pre-relocation"),
    (lambda d: d["approval_required_paths"].append("testenv.toml"), "pre-relocation"),
    (lambda d: d["protected_paths"].remove(LOCAL_TOML), f"protected_paths must cover '{LOCAL_TOML}'"),
    (lambda d: d["protected_paths"].remove("test_environment/mxissues/**"),
     "protected_paths must cover 'test_environment/mxissues/**'"),
    (lambda d: d["approval_required_paths"].remove(TOML), f"approval_required_paths must cover '{TOML}'"),
    (lambda d: d["roles"]["automation"]["excluded_paths"].remove(TOML), f"role 'automation' owns '{TOML}'"),
], ids=["pre-relocation-file", "old-beside-new", "stale-exclusion", "stale-approval", "local-toml-unprotected",
        "mxissues-unprotected", "toml-not-approval", "toml-owned"])
def test_a_stale_or_incomplete_test_environment_policy_is_refused(mutate, match: str) -> None:
    data = copy.deepcopy(example_data())
    mutate(data)
    with pytest.raises(ConfigError, match=re.escape(match)) as excinfo:
        config_from_mapping(data)
    assert "scripts/migrate_project_config.py" in str(excinfo.value)
    assert (SCRIPTS_DIR / "migrate_project_config.py").is_file()


def test_a_stricter_test_environment_policy_is_accepted() -> None:
    data = copy.deepcopy(example_data())
    data["roles"]["automation"]["excluded_paths"].remove(LOCAL_TOML)  # protected anyway
    assert config_from_mapping(data).role("automation").excluded[-1] == "scripts/deploy.sh"
    data["approval_required_paths"].remove(TOML)  # never writable instead of approval-only
    data["roles"]["automation"]["excluded_paths"].remove(TOML)
    data["protected_paths"].append(TOML)
    assert TOML in config_from_mapping(data).protected


def test_roles_must_be_exactly_the_five_specializations(config: ProjectConfig) -> None:
    roles = dict(config.roles)
    roles.pop("coordination")
    with pytest.raises(ConfigError, match="exactly"):
        dataclasses.replace(config, roles=roles)
    roles = dict(config.roles)
    roles["cli"] = RoleConfig("cli", "missing", ("cli/**",))
    with pytest.raises(ConfigError, match="unknown provider"):
        dataclasses.replace(config, roles=roles)


def test_branch_names(config: ProjectConfig) -> None:
    assert config.branch_for("items-export") == "feature/items-export"
    for bad in ("Items", "../x", "a b", ""):
        with pytest.raises(ConfigError):
            config.branch_for(bad)


def test_qwen_profile_is_exact_and_budget_is_coherent() -> None:
    assert REMOTE_QWEN.default_model == "Qwen3.6-35B-A3B-Q4_K_M.gguf"
    assert REMOTE_QWEN.display_name == "Qwen 3.6 35B A3B"
    assert (REMOTE_QWEN.context_window_tokens, REMOTE_QWEN.max_output_tokens,
            REMOTE_QWEN.declared_max_input_tokens) == (131072, 16384, 128000)
    assert REMOTE_QWEN.hard_max_input_tokens == 114688
    assert REMOTE_QWEN.effective_max_input_tokens == 110592
    assert REMOTE_QWEN.temperature == 0.2
    assert REMOTE_QWEN.client_edit_tools == ("apply-patch", "multi-find-replace", "find-replace")


def test_declared_capabilities_are_not_enabled_until_validated() -> None:
    assert all(REMOTE_QWEN.declared_capabilities.values())
    assert not any(REMOTE_QWEN.capability_enabled(c) for c in ("tool_calling", "vision", "streaming", "thinking"))
    validated = dataclasses.replace(REMOTE_QWEN, validated_capabilities=frozenset({"tool_calling"}))
    assert validated.capability_enabled("tool_calling") and not validated.capability_enabled("streaming")
    with pytest.raises(ProviderError):
        dataclasses.replace(REMOTE_QWEN, validated_capabilities=frozenset({"teleport"}))


@pytest.mark.parametrize("configured,expected", [
    ("http://10.0.0.5:8080/v1", "http://10.0.0.5:8080/v1/chat/completions"),
    ("http://10.0.0.5:8080/v1/", "http://10.0.0.5:8080/v1/chat/completions"),
    ("http://10.0.0.5:8080/v1/chat/completions", "http://10.0.0.5:8080/v1/chat/completions"),
    ("https://models.example.invalid/v1", "https://models.example.invalid/v1/chat/completions"),
])
def test_chat_completions_suffix_is_appended_exactly_once(configured: str, expected: str) -> None:
    assert chat_completions_url(configured) == expected


@pytest.mark.parametrize("bad", [
    "http://h/v1/v1", "http://h/v1/chat/completions/chat/completions", "ftp://h/v1", "http://h/v1?x=1",
    "http://user:pw@h/v1", "not a url",
])
def test_malformed_endpoints_are_rejected(bad: str) -> None:
    with pytest.raises(ProviderError):
        chat_completions_url(bad)


def test_endpoint_comes_from_the_environment_only() -> None:
    with pytest.raises(ProviderUnavailableError, match="AGENT_QWEN_BASE_URL"):
        resolve_endpoint(REMOTE_QWEN, {})
    endpoint = resolve_endpoint(REMOTE_QWEN, {"AGENT_QWEN_BASE_URL": "https://m.example.invalid/v1",
                                             "AGENT_QWEN_API_KEY": "sk-secret-123"})
    assert endpoint.model == REMOTE_QWEN.default_model
    assert "sk-secret-123" not in repr(endpoint)
    override = resolve_endpoint(REMOTE_QWEN, {"AGENT_QWEN_BASE_URL": "https://m.example.invalid/v1",
                                             "AGENT_QWEN_MODEL": "other-model"})
    assert override.model == "other-model"


def test_transport_and_disclosure_gates() -> None:
    plain = resolve_endpoint(REMOTE_QWEN, {"AGENT_QWEN_BASE_URL": "http://10.0.0.5:8080/v1"})
    with pytest.raises(TransportPolicyError, match="D-15"):
        check_transport(REMOTE_QWEN, plain, carries_code=False)
    excepted = dataclasses.replace(REMOTE_QWEN, plain_http_exception=True)
    check_transport(excepted, plain, carries_code=False)
    with pytest.raises(TransportPolicyError, match="D-16"):
        check_transport(excepted, plain, carries_code=True)
    tls = resolve_endpoint(REMOTE_QWEN, {"AGENT_QWEN_BASE_URL": "https://m.example.invalid/v1"})
    with pytest.raises(TransportPolicyError, match="D-16"):
        check_transport(REMOTE_QWEN, tls, carries_code=True)
    check_transport(dataclasses.replace(REMOTE_QWEN, code_disclosure_allowed=True), tls, carries_code=True)
    loopback = resolve_endpoint(REMOTE_QWEN, {"AGENT_QWEN_BASE_URL": "http://127.0.0.1:8080/v1"})
    check_transport(REMOTE_QWEN, loopback, carries_code=True)  # local mock servers are allowed


def test_context_budget() -> None:
    small = [{"role": "user", "content": "hello"}]
    assert check_budget(REMOTE_QWEN, small, [], 1024) > 0
    with pytest.raises(ContextBudgetError, match="max_tokens"):
        check_budget(REMOTE_QWEN, small, [], 16385)
    huge = [{"role": "user", "content": "x" * (3 * 110_592 + 30)}]
    with pytest.raises(ContextBudgetError, match="110592"):
        check_budget(REMOTE_QWEN, huge, [], 1024)


def test_redaction() -> None:
    assert redact("auth failed for sk-123", ["sk-123", None, ""]) == "auth failed for ***"
