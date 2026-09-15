"""Unit tests for ConfigurationService orchestration, merge helpers, and name validation."""

# pylint: disable=missing-function-docstring,too-few-public-methods,redefined-outer-name

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from src.services.acceptance import ServiceAcceptCriteria
from src.services.configuration.merge import deep_merge, prune_payload
from src.services.configuration.models import (
    ConfigurationFileResult,
    ConfigurationMutationResult,
    ConfigurationNotFoundError,
    ConfigurationProvider,
    ConfigurationProviderUnavailableError,
    ConfigurationSummary,
    InvalidConfigurationNameError,
)
from src.services.configuration.providers.file_system import FileSystemConfigurationProvider
from src.services.configuration import service as configuration_service
from src.services.configuration.service import LOG_SOURCE, ConfigurationService
from src.services.logging.models import LogLevel
from src.services.models import ServiceProvider


# ---------------------------------------------------------------------------
# Stub helpers
# ---------------------------------------------------------------------------

class _StubProvider(ConfigurationProvider):
    """Minimal ConfigurationProvider stub with recorded calls."""

    def __init__(
            self,
            base: Path,
            *,
            name: str = "stub",
            provider_name: str = "file_system",
            enabled: bool = True,
    ) -> None:
        super().__init__(
            name=name,
            service_name="configuration",
            provider_name=provider_name,
            provider_category="",
            enabled=enabled,
            priority=1,
            config={},
            config_path=base / f"{name}.json",
            base_dir=base,
        )
        self.calls: dict[str, list[Any]] = {}

    def _record(self, method: str, *args: Any) -> None:
        self.calls.setdefault(method, []).append(args)

    def list_configurations(self, service_name: str) -> list[ConfigurationSummary]:
        self._record("list_configurations", service_name)
        return [
            ConfigurationSummary(
                config_name="test_cfg",
                filename="test_cfg.json",
                provider="file_system",
                enabled=True,
                priority=1,
                valid_json=True,
            )
        ]

    def get_configuration(self, service_name: str, config_name: str) -> ConfigurationFileResult:
        self._record("get_configuration", service_name, config_name)
        return ConfigurationFileResult(
            config_name=config_name,
            filename=f"{config_name}.json",
            payload={"service_provider": {"service": service_name}},
        )

    def add_configuration(
            self, service_name: str, config_name: str, payload: dict[str, Any]
    ) -> ConfigurationMutationResult:
        self._record("add_configuration", service_name, config_name, payload)
        return ConfigurationMutationResult(config_name=config_name, changed=True)

    def remove_configuration(self, service_name: str, config_name: str) -> ConfigurationMutationResult:
        self._record("remove_configuration", service_name, config_name)
        return ConfigurationMutationResult(config_name=config_name, changed=True)

    def patch_configuration(
            self, service_name: str, config_name: str, patch: dict[str, Any]
    ) -> ConfigurationMutationResult:
        self._record("patch_configuration", service_name, config_name, patch)
        return ConfigurationMutationResult(config_name=config_name, changed=True)

    def set_configurations_enabled(
            self, service_name: str, config_names: list[str], enabled: bool
    ) -> list[ConfigurationMutationResult]:
        self._record("set_configurations_enabled", service_name, config_names, enabled)
        return [ConfigurationMutationResult(config_name=n, changed=True) for n in config_names]

    def set_all_configurations_enabled(
            self, service_name: str, enabled: bool
    ) -> list[ConfigurationMutationResult]:
        self._record("set_all_configurations_enabled", service_name, enabled)
        return [ConfigurationMutationResult(config_name="cfg", changed=True)]


class _CapturedRecords:
    """Capture the records the service posts to the logging API."""

    def __init__(self) -> None:
        self.records: list[tuple[str, str, LogLevel, dict[str, Any]]] = []

    def __call__(self, service: str, event: str, level: LogLevel, **fields: Any) -> None:
        self.records.append((service, event, level, fields))


@pytest.fixture
def captured_logs(monkeypatch: pytest.MonkeyPatch) -> _CapturedRecords:
    """Intercept "emit_event_nowait" at the seam the service imports it through."""
    recorder = _CapturedRecords()
    monkeypatch.setattr(configuration_service, "emit_event_nowait", recorder)
    return recorder


def _make_service(tmp_path: Path, providers: list[Any] | None = None) -> ConfigurationService:
    svc = ConfigurationService(
        service_name="configuration",
        package_name="tests",
        service_dir=tmp_path,
        base_dir=tmp_path,
    )
    if providers is not None:
        svc.replace_providers(providers)
    return svc


# ---------------------------------------------------------------------------
# ConfigurationService — provider management
# ---------------------------------------------------------------------------

def test_replace_providers_keeps_configuration_providers(tmp_path: Path) -> None:
    stub = _StubProvider(tmp_path)
    foreign = ServiceProvider(
        name="other", service_name="x", provider_name="x", provider_category="",
        enabled=True, priority=1, config={},
        config_path=tmp_path / "other.json", base_dir=tmp_path,
    )
    svc = _make_service(tmp_path, [stub, foreign])
    assert svc.providers_snapshot() == [stub]


def test_replace_providers_empty_list(tmp_path: Path) -> None:
    svc = _make_service(tmp_path, [])
    assert not svc.providers_snapshot()


def test_providers_snapshot_returns_copy(tmp_path: Path) -> None:
    stub = _StubProvider(tmp_path)
    svc = _make_service(tmp_path, [stub])
    snap1 = svc.providers_snapshot()
    snap2 = svc.providers_snapshot()
    assert snap1 == snap2
    assert snap1 is not snap2


def test_all_providers_includes_disabled(tmp_path: Path) -> None:
    active = _StubProvider(tmp_path, name="a", enabled=True)
    disabled = _StubProvider(tmp_path, name="b", enabled=False)
    svc = _make_service(tmp_path, [active, disabled])
    assert len(svc.get_all_providers()) == 2


def test_active_providers_excludes_disabled(tmp_path: Path) -> None:
    active = _StubProvider(tmp_path, name="a", enabled=True)
    disabled = _StubProvider(tmp_path, name="b", enabled=False)
    svc = _make_service(tmp_path, [active, disabled])
    assert svc.get_active_providers() == [active]


# ---------------------------------------------------------------------------
# ConfigurationService — provider selection
# ---------------------------------------------------------------------------

def test_select_provider_raises_when_no_providers(tmp_path: Path) -> None:
    svc = _make_service(tmp_path, [])
    with pytest.raises(ConfigurationProviderUnavailableError):
        svc.list_configurations("text_to_speech")


def test_select_provider_prefers_file_system_provider(tmp_path: Path) -> None:
    fs = _StubProvider(tmp_path, name="file_system", provider_name="file_system", enabled=True)
    other = _StubProvider(tmp_path, name="other", provider_name="custom", enabled=True)
    svc = _make_service(tmp_path, [other, fs])
    svc.list_configurations("text_to_speech")
    assert "list_configurations" in fs.calls
    assert "list_configurations" not in other.calls


def test_select_provider_falls_back_to_first_when_no_file_system(tmp_path: Path) -> None:
    first = _StubProvider(tmp_path, name="first", provider_name="custom_a", enabled=True)
    second = _StubProvider(tmp_path, name="second", provider_name="custom_b", enabled=True)
    svc = _make_service(tmp_path, [first, second])
    svc.list_configurations("text_to_speech")
    assert "list_configurations" in first.calls
    assert "list_configurations" not in second.calls


def test_select_provider_returns_disabled_file_system_when_no_active(tmp_path: Path) -> None:
    fs_disabled = _StubProvider(tmp_path, name="file_system", provider_name="file_system", enabled=False)
    svc = _make_service(tmp_path, [fs_disabled])
    svc.list_configurations("svc")
    assert "list_configurations" in fs_disabled.calls


# ---------------------------------------------------------------------------
# ConfigurationService — delegation methods
# ---------------------------------------------------------------------------

def test_list_configurations_delegates_to_provider(tmp_path: Path) -> None:
    stub = _StubProvider(tmp_path)
    svc = _make_service(tmp_path, [stub])
    results = svc.list_configurations("text_to_speech")
    assert len(results) == 1
    assert results[0].config_name == "test_cfg"
    assert stub.calls["list_configurations"] == [("text_to_speech",)]


def test_operations_are_info_logged_through_logging_api(
        tmp_path: Path, captured_logs: _CapturedRecords,
) -> None:
    stub = _StubProvider(tmp_path)
    svc = _make_service(tmp_path, [stub])

    svc.get_configuration("logging", "default")

    source, event, level, fields = captured_logs.records[0]
    assert source == LOG_SOURCE
    assert level is LogLevel.INFO
    assert event == "configuration_file_operation"
    assert fields == {
        "operation": "read",
        "service_name": "logging",
        "config_name": "default",
    }


def test_operation_errors_are_error_logged_and_reraised(
        tmp_path: Path, captured_logs: _CapturedRecords,
) -> None:
    svc = _make_service(tmp_path, [])

    with pytest.raises(ConfigurationProviderUnavailableError):
        svc.list_configurations("logging")

    assert [level for _, _, level, _ in captured_logs.records] == [LogLevel.INFO, LogLevel.ERROR]
    assert captured_logs.records[-1][1] == "configuration_operation_failed"
    assert captured_logs.records[-1][3]["error_type"] == "ConfigurationProviderUnavailableError"



def test_get_configuration_delegates(tmp_path: Path) -> None:
    stub = _StubProvider(tmp_path)
    svc = _make_service(tmp_path, [stub])
    result = svc.get_configuration("logging", "default")
    assert result.config_name == "default"
    assert result.filename == "default.json"
    assert stub.calls["get_configuration"] == [("logging", "default")]


def test_add_configuration_delegates(tmp_path: Path) -> None:
    stub = _StubProvider(tmp_path)
    svc = _make_service(tmp_path, [stub])
    result = svc.add_configuration("svc", "my_cfg", {"key": "val"})
    assert result.changed is True
    assert result.config_name == "my_cfg"
    assert stub.calls["add_configuration"] == [("svc", "my_cfg", {"key": "val"})]


def test_remove_configuration_delegates(tmp_path: Path) -> None:
    stub = _StubProvider(tmp_path)
    svc = _make_service(tmp_path, [stub])
    result = svc.remove_configuration("svc", "my_cfg")
    assert result.changed is True
    assert stub.calls["remove_configuration"] == [("svc", "my_cfg")]


def test_patch_configuration_delegates(tmp_path: Path) -> None:
    stub = _StubProvider(tmp_path)
    svc = _make_service(tmp_path, [stub])
    result = svc.patch_configuration("svc", "my_cfg", {"enabled": False})
    assert result.changed is True
    assert stub.calls["patch_configuration"] == [("svc", "my_cfg", {"enabled": False})]


def test_enable_configurations_delegates(tmp_path: Path) -> None:
    stub = _StubProvider(tmp_path)
    svc = _make_service(tmp_path, [stub])
    results = svc.enable_configurations("svc", ["cfg_a", "cfg_b"])
    assert len(results) == 2
    assert all(r.changed for r in results)
    assert stub.calls["set_configurations_enabled"] == [("svc", ["cfg_a", "cfg_b"], True)]


def test_disable_configurations_delegates(tmp_path: Path) -> None:
    stub = _StubProvider(tmp_path)
    svc = _make_service(tmp_path, [stub])
    svc.disable_configurations("svc", ["cfg_a"])
    assert stub.calls["set_configurations_enabled"] == [("svc", ["cfg_a"], False)]


def test_enable_all_configurations_delegates(tmp_path: Path) -> None:
    stub = _StubProvider(tmp_path)
    svc = _make_service(tmp_path, [stub])
    svc.enable_all_configurations("svc")
    assert stub.calls["set_all_configurations_enabled"] == [("svc", True)]


def test_disable_all_configurations_delegates(tmp_path: Path) -> None:
    stub = _StubProvider(tmp_path)
    svc = _make_service(tmp_path, [stub])
    svc.disable_all_configurations("svc")
    assert stub.calls["set_all_configurations_enabled"] == [("svc", False)]


# ---------------------------------------------------------------------------
# deep_merge
# ---------------------------------------------------------------------------

def test_deep_merge_flat_keys() -> None:
    result = deep_merge({"a": 1, "b": 2}, {"b": 99, "c": 3})
    assert result == {"a": 1, "b": 99, "c": 3}


def test_deep_merge_nested_dicts_are_merged_recursively() -> None:
    base = {"outer": {"x": 1, "y": 2}}
    patch = {"outer": {"y": 99, "z": 3}}
    assert deep_merge(base, patch) == {"outer": {"x": 1, "y": 99, "z": 3}}


def test_deep_merge_patch_scalar_overwrites_nested_dict() -> None:
    base = {"outer": {"x": 1}}
    patch = {"outer": "flat"}
    assert deep_merge(base, patch) == {"outer": "flat"}


def test_deep_merge_does_not_mutate_inputs() -> None:
    base = {"a": {"b": 1}}
    patch = {"a": {"c": 2}}
    result = deep_merge(base, patch)
    assert base == {"a": {"b": 1}}
    assert patch == {"a": {"c": 2}}
    assert result == {"a": {"b": 1, "c": 2}}


def test_deep_merge_empty_patch() -> None:
    base = {"a": 1}
    assert deep_merge(base, {}) == {"a": 1}


def test_deep_merge_empty_base() -> None:
    patch = {"a": 1}
    assert deep_merge({}, patch) == {"a": 1}


# ---------------------------------------------------------------------------
# prune_payload
# ---------------------------------------------------------------------------

def test_prune_payload_removes_none_values() -> None:
    payload = {"a": 1, "b": None}
    assert prune_payload(payload) == {"a": 1}


def test_prune_payload_removes_empty_strings() -> None:
    payload = {"a": "hello", "b": ""}
    assert prune_payload(payload) == {"a": "hello"}


def test_prune_payload_removes_empty_dicts() -> None:
    payload = {"a": 1, "b": {}}
    assert prune_payload(payload) == {"a": 1}


def test_prune_payload_removes_empty_lists() -> None:
    payload = {"a": [1, 2], "b": []}
    assert prune_payload(payload) == {"a": [1, 2]}


def test_prune_payload_removes_default_priority_999() -> None:
    payload = {"service_provider": {"priority": 999, "enabled": True}}
    result = prune_payload(payload)
    assert "priority" not in result["service_provider"]
    assert result["service_provider"]["enabled"] is True


def test_prune_payload_keeps_non_default_priority() -> None:
    payload = {"service_provider": {"priority": 5, "enabled": True}}
    result = prune_payload(payload)
    assert result["service_provider"]["priority"] == 5


def test_prune_payload_nested_empty_values_removed() -> None:
    payload = {"outer": {"inner": None, "keep": "yes"}}
    assert prune_payload(payload) == {"outer": {"keep": "yes"}}


def test_prune_payload_list_items_pruned() -> None:
    payload = {"items": [None, "keep", ""]}
    assert prune_payload(payload) == {"items": ["keep"]}


# ---------------------------------------------------------------------------
# FileSystemConfigurationProvider — disk reads
# ---------------------------------------------------------------------------

class _FakeBuilder:
    """Registry-builder stub exposing accept criteria for disk-read tests."""

    def __init__(self, config_subdir: str = "logging") -> None:
        self._criteria = ServiceAcceptCriteria(
            service_name="logging",
            config_subdir=config_subdir,
            validate_provider=lambda provider: None,
            build_provider=lambda provider, base_dir: provider,
        )

    def get_accept_criteria(self) -> dict[str, ServiceAcceptCriteria]:
        return {"logging": self._criteria}


def _make_fs_provider(tmp_path: Path) -> FileSystemConfigurationProvider:
    return FileSystemConfigurationProvider.build_runtime(tmp_path, _FakeBuilder())  # type: ignore[arg-type]


_DISK_PAYLOAD: dict[str, Any] = {
    "service_provider": {
        "service": "logging",
        "provider": "struct_log",
        "enabled": True,
        "priority": 20,
        "version": "1.0",
        "config": {"structlog": {"renderer": "console"}},
    }
}


def _write_config(tmp_path: Path, name: str, payload: dict[str, Any]) -> Path:
    config_dir = tmp_path / "logging"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / f"{name}.json"
    config_path.write_text(json.dumps(payload), encoding="utf-8")
    return config_path


def test_fs_get_configuration_returns_full_payload(tmp_path: Path) -> None:
    _write_config(tmp_path, "default", _DISK_PAYLOAD)
    provider = _make_fs_provider(tmp_path)
    result = provider.get_configuration("logging", "default")
    assert result.config_name == "default"
    assert result.filename == "default.json"
    assert result.payload == _DISK_PAYLOAD


def test_fs_get_configuration_missing_raises_not_found(tmp_path: Path) -> None:
    provider = _make_fs_provider(tmp_path)
    with pytest.raises(ConfigurationNotFoundError):
        provider.get_configuration("logging", "missing")


def test_fs_list_configurations_includes_version(tmp_path: Path) -> None:
    _write_config(tmp_path, "default", _DISK_PAYLOAD)
    provider = _make_fs_provider(tmp_path)
    summaries = provider.list_configurations("logging")
    assert len(summaries) == 1
    assert summaries[0].version == "1.0"
    assert summaries[0].priority == 20
    assert summaries[0].enabled is True


def test_fs_list_configurations_version_absent_is_none(tmp_path: Path) -> None:
    payload = {"service_provider": {"service": "logging", "provider": "struct_log", "enabled": True}}
    _write_config(tmp_path, "no_version", payload)
    provider = _make_fs_provider(tmp_path)
    summaries = provider.list_configurations("logging")
    assert summaries[0].version is None


# ---------------------------------------------------------------------------
# FileSystemConfigurationProvider._validate_config_name
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", [
    "my_config",
    "my-config",
    "my.config",
    "MyConfig123",
    "config.json",  # .json suffix is stripped
])
def test_validate_config_name_valid(name: str) -> None:
    result = FileSystemConfigurationProvider._validate_config_name(name)  # pylint: disable=protected-access
    assert result == name.rstrip(".json").rstrip() if name.endswith(".json") else result


@pytest.mark.parametrize("name", [
    "",
    "   ",
    "has space",
    "has/slash",
    "has\\backslash",
    "../traversal",
    "config!",
    "config@name",
])
def test_validate_config_name_invalid(name: str) -> None:
    with pytest.raises(InvalidConfigurationNameError):
        FileSystemConfigurationProvider._validate_config_name(name)  # pylint: disable=protected-access


def test_validate_config_name_strips_json_suffix() -> None:
    result = FileSystemConfigurationProvider._validate_config_name("myconfig.json")  # pylint: disable=protected-access
    assert result == "myconfig"
