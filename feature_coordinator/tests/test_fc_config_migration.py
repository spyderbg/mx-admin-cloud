"""scripts/migrate_project_config.py rewrites a pre-relocation machine-local project file in place.

The test environment moved into test_environment/ on 2026-10-30, and the Worker refuses a project
file that still names the old locations. Every case runs on a temporary copy of the documented
example as it read before the move; no machine-local configuration is read or written."""

# pylint: disable=missing-function-docstring

from __future__ import annotations

import difflib
import stat
from pathlib import Path

import pytest
import yaml

from feature_coordinator.config import default_config, load_config

from fc_helpers import EXAMPLE_CONFIG, load_script, pre_relocation_example

mig = load_script("migrate_project_config")

POLICY_LISTS = ("protected_paths", "approval_required_paths")


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "project.yaml"
    path.write_text(text, encoding="utf-8")
    path.chmod(0o600)
    return path


def backups(tmp_path: Path) -> list[Path]:
    return sorted(tmp_path.glob("project.yaml.bak-*"))


def policy(path: Path) -> tuple:
    config = load_config(path)
    return config.protected, config.approval_required, config.role("automation").excluded


def test_the_pre_relocation_example_becomes_the_current_one(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    before = pre_relocation_example()
    path = write(tmp_path, before)
    assert mig.main([str(path)]) == 1  # dry run: the diff, nothing written
    assert '+  - "test_environment/testenv.local.toml"' in capsys.readouterr().out
    assert path.read_text(encoding="utf-8") == before and not backups(tmp_path)

    assert mig.main(["--apply", str(path)]) == 0
    assert path.read_text(encoding="utf-8") == EXAMPLE_CONFIG.read_text(encoding="utf-8")
    [backup] = backups(tmp_path)
    assert backup.read_text(encoding="utf-8") == before
    assert stat.S_IMODE(path.stat().st_mode) == stat.S_IMODE(backup.stat().st_mode) == 0o600
    assert load_config(path) == default_config()

    capsys.readouterr()
    assert mig.main(["--apply", str(path)]) == 0  # idempotent: nothing left, no second backup
    assert "already migrated" in capsys.readouterr().out
    assert backups(tmp_path) == [backup] and path.read_text(encoding="utf-8") == EXAMPLE_CONFIG.read_text("utf-8")


def test_unrelated_settings_and_comments_are_kept(tmp_path: Path) -> None:
    before = pre_relocation_example().replace(
        "    enabled: false                       # `python -m feature_coordinator worker start` refuses to run "
        "until this is true",
        "    enabled: true                        # operator: desktop Worker").replace(
        "# Never writable by any role", "# operator note kept verbatim\n# Never writable by any role")
    path = write(tmp_path, before)
    assert mig.main(["--apply", str(path)]) == 0
    after = path.read_text(encoding="utf-8")
    old, new = yaml.safe_load(before), yaml.safe_load(after)
    for data in (old, new):
        for key in POLICY_LISTS:
            data.pop(key)
        data["roles"]["automation"].pop("excluded_paths")
    assert old == new and new["worker"]["autonomous"]["enabled"] is True
    changed = [line for line in difflib.ndiff(before.splitlines(), after.splitlines()) if line[:2] in ("- ", "+ ")]
    assert changed and all("test_environment/" in line or "scripts/mx" in line or "testenv.toml" in line
                           for line in changed), changed
    assert "# operator note kept verbatim" in after
    assert policy(path) == policy(EXAMPLE_CONFIG)


def test_other_list_styles_duplicates_and_a_missing_exclusion_list(tmp_path: Path) -> None:
    text = pre_relocation_example()
    text = text.replace('  - "scripts/mxfix/**"                   # independent bug-finding/repair system',
                        "  - 'scripts/mxfix/**'\n  # a comment between entries stays")
    text = text.replace('  - "scripts/mxissues/**"                # independent bug-finding/repair system',
                        "  - scripts/mxissues/**\n  - test_environment/mxissues/**")  # unquoted, and both spellings
    text = text.replace('  - "testenv.toml"\n', '  - "testenv.toml"\n  - "test_environment/testenv.toml"\n')
    text = text.replace('    excluded_paths: ["scripts/mxfix/**", "scripts/mxissues/**", "scripts/deploy.sh"]\n', "")
    path = write(tmp_path, text)
    assert mig.main(["--apply", str(path)]) == 0
    after = path.read_text(encoding="utf-8")
    assert "  # a comment between entries stays" in after and "'test_environment/mxfix/**'" in after
    config = load_config(path)
    assert config.protected == default_config().protected
    assert config.approval_required == default_config().approval_required
    # The role had no excluded_paths: it gets one, after owned_paths, with the two toml files only.
    assert config.role("automation").excluded == ("test_environment/testenv.toml",
                                                  "test_environment/testenv.local.toml")
    assert mig.main(["--apply", str(path)]) == 0 and len(backups(tmp_path)) == 1


@pytest.mark.parametrize("change,message", [
    (lambda t: t.replace("protected_paths:\n", "protected_paths: &protected\n"), "neither a [flow] nor a block list"),
    (lambda t: t.replace("max_writers: 1", "max_writers: 2", 1), "does not validate"),
    (lambda t: "just: a mapping\n", "not a Worker project configuration"),
    (lambda t: "roles: [\n", "not valid YAML"),
], ids=["yaml-anchor", "invalid-result", "not-a-config", "broken-yaml"])
def test_what_cannot_be_migrated_safely_is_left_untouched(tmp_path: Path, capsys: pytest.CaptureFixture[str],
                                                         change, message: str) -> None:
    text = change(pre_relocation_example())
    path = write(tmp_path, text)
    assert mig.main(["--apply", str(path)]) == 2
    assert message in capsys.readouterr().err
    assert path.read_text(encoding="utf-8") == text and not backups(tmp_path)


def test_a_missing_file_is_an_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert mig.main([str(tmp_path / "absent.yaml")]) == 2 and "error:" in capsys.readouterr().err
