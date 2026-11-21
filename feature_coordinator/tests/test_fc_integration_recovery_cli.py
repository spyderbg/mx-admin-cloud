"""W4.7 (M6): `integration recover`, `integration schedule` and `worker status` through the real CLI
(integration_recovery_commands.py). The jobs come from the M2–M5 rig over the configuration FILE the CLI loads; the
scripted GitHub stands behind every client the commands build. No network, no real GitHub, no real merge."""

# pylint: disable=missing-function-docstring,redefined-outer-name,protected-access,unused-argument,unused-import
# pylint: disable=too-many-locals

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from feature_coordinator import github_commands
from feature_coordinator.cli import main as cli_main
from feature_coordinator.config import load_config
from feature_coordinator.integration import JobState
from feature_coordinator.integration_schedule import STAGES, stage_argv
from feature_coordinator.issue_sync import SimulatedCrash

from fc_integration_merge_rig import approve_merge, github_for, merge
from fc_integration_rig import BRANCH, OperatorKey, Rig, change_on, github_origin, live_client, origin, request_for
from fc_integration_worker_rig import Clock, Stages, run_until_quiet, schedule
from test_fc_integration_merge_e2e import _cli_config, drive_m1_to_m4
from test_fc_integration_review import X_PY, launchers
from test_fc_integration_validate import FakeGates, key, seeded


@pytest.fixture
def cli(seeded: Path, state_dir: Path, key: OperatorKey, tmp_path: Path, github_origin: Path,
        monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    """(rig, fake, run): the rig over the configuration file the CLI loads — with the Worker's scheduling ON in that
    file — and `run(*args)` → (exit code, stdout) of `python -m feature_coordinator … integration ARGS`."""
    path = _cli_config(tmp_path, key)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["github"]["integration"]["worker"] = {"enabled": True, "stages": ["inspect", "validate", "review", "recover"],
                                              "retry_seconds": 60, "max_stalls": 1}
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    config = load_config(path)
    config = dataclasses.replace(config, runtime=dataclasses.replace(config.runtime, terminate_grace_seconds=2))
    rig = Rig(seeded, state_dir, key, config=config)
    fake = github_for(rig, github_origin)
    monkeypatch.setattr(github_commands, "_tracker",
                        lambda _args, _config, _state: live_client(fake, integration=rig.config.github.integration))
    base = ["--tracker", "github", "--repo", str(rig.repo), "--state-dir", str(rig.state), "--project-config",
            str(path)]

    def run(*args: str, group: str = "integration") -> tuple[int, str]:
        code = cli_main([*base, group, *args])
        return code, capsys.readouterr().out
    return rig, fake, run


def test_recover_diagnoses_read_only_and_settles_an_interrupted_merge_without_writing(cli: Any, launchers: Any):
    rig, fake, run = cli
    sha = change_on(rig.repo, BRANCH, {"backend/src/x.py": X_PY})
    job = drive_m1_to_m4(rig, launchers, sha)
    assert approve_merge(rig, job)["outcome"] == "recorded"
    with pytest.raises(SimulatedCrash):
        merge(rig, job, faults=("after-merge-request",))                  # GitHub merged; the run died
    writes, record = list(fake.writes()), (rig.state / "integration" / f"{job}.json").read_bytes()
    calls = len(fake.calls)
    code, out = run("recover")                                            # every open job, read-only
    assert code == 0 and f"{job}  AWAITING_MERGE_APPROVAL" in out and "interrupted: merge" in out
    assert "GitHub write: unknown" in out and "diagnosis only" in out and "never sent again" in out
    code, out = run("recover", "--job", job, "--json")
    data = json.loads(out)
    assert code == 0 and data["outcome"] == "diagnosed" and data["applied"] is False
    found = data["jobs"][0]
    assert found["interrupted"]["operation"] == "merge" and found["interrupted"]["github_write"] == "unknown"
    assert found["next"]["stage"] == "recover" and found["next"]["who"] == "worker"
    assert found["command_running"] is False
    assert found["pinned"]["source"].endswith(sha) and found["evidence"]["merge_requests"][0]["state"] == "dispatched"
    assert len(fake.calls) == calls and (rig.state / "integration" / f"{job}.json").read_bytes() == record
    with pytest.raises(SystemExit):
        cli_main(["integration", "recover", "--bogus"])
    code, out = run("recover", "--apply", "--json")                       # --apply needs ONE job
    assert code == 1 and "pass --job" in json.loads(out)["error"]["message"]
    code, out = run("recover", "--job", job, "--apply", "--actor", "spyderbg", "--json")
    data = json.loads(out)
    assert code == 0 and data["outcome"] == "completed" and data["changed"] is True
    assert rig.state_of(job) is JobState.COMPLETED and fake.writes() == writes        # recover wrote nothing
    assert [h["actor"] for h in rig.jobs.load(job).history[-2:]] == ["spyderbg", "spyderbg"]
    code, out = run("recover", "--job", job)                              # terminal: nothing left
    assert code == 0 and "COMPLETED" in out and "interrupted" not in out
    code, out = run("recover")
    assert code == 0 and "no open integration jobs" in out


def test_schedule_shows_the_workers_view_and_resume_is_a_recorded_operator_decision(cli: Any, launchers: Any):
    rig, _fake, run = cli
    sha = change_on(rig.repo, BRANCH, {"backend/src/x.py": X_PY})
    job = rig.approved(request_for(rig.repo, sha)).job_id
    code, out = run("schedule")
    assert code == 0 and "Worker scheduling of integration jobs: ON" in out and "the merge is never scheduled" in out
    assert f"{job}  AWAITING_APPROVAL" in out and "next (Worker)" in out
    runner, clock = Stages(rig, gates=FakeGates(), launcher=launchers("pass")), Clock()
    runner.scripted["inspect"] = ("deferred", "GitHub is unreachable")
    run_until_quiet(rig, runner, clock)                                   # one stall: held (max_stalls 1)
    runner.cleanup()
    code, out = run("schedule", "--job", job, "--json")
    data = json.loads(out)
    assert code == 0 and data["jobs"][0]["category"] == "held" and "`deferred`: GitHub is unreachable" in \
        data["jobs"][0]["hold"]["reason"]
    code, out = run("status", group="worker")                             # `worker status` shows it too
    assert f"integration {job} (AWAITING_APPROVAL): held" in out and "--resume" in out
    for args in (("schedule", "--resume"), ("schedule", "--resume", "--job", job), ("schedule", "--reason", "x"),
                 ("schedule", "--apply")):
        code, out = run(*args, "--json")
        assert code == 1 and json.loads(out)["ok"] is False
    code, out = run("schedule", "--job", job, "--resume", "--reason", "GitHub is back")
    assert code == 0 and "would-resume" in out and "dry run" in out and schedule(rig, job).hold is not None
    code, out = run("schedule", "--job", job, "--resume", "--reason", "GitHub is back", "--actor", "spyderbg",
                    "--apply")
    assert code == 0 and "schedule: resumed" in out and schedule(rig, job).hold is None
    event = [e for e in rig.store.events() if e["kind"] == "integration-schedule-resumed"][0]
    assert (event["job"], event["by"], event["reason"]) == (job, "spyderbg", "GitHub is back")
    code, out = run("schedule", "--job", "int-20990101-0001", "--resume", "--reason", "x", "--apply", "--json")
    assert code == 1                                                       # an unknown job is refused


def test_the_integration_help_lists_recover_and_schedule_and_no_merge_option_exists_there(capsys: Any):
    for command in ("recover", "schedule"):
        with pytest.raises(SystemExit) as stop:
            cli_main(["integration", command, "--help"])
        text = capsys.readouterr().out
        assert stop.value.code == 0 and "--job" in text and "--retry" not in text and "--merge" not in text
    with pytest.raises(SystemExit):
        cli_main(["integration", "--help"])
    text = capsys.readouterr().out
    assert "integration recover" in text and "integration schedule" in text and "never the merge" in text


@pytest.mark.parametrize("stage", STAGES)
def test_every_scheduled_command_line_is_one_the_real_cli_accepts(stage: str, repo: Path, state_dir: Path,
                                                                  capsys: pytest.CaptureFixture[str]):
    """What the Worker starts is parsed by the real CLI as that stage's command (no usage error), and then judged by
    the command itself — here refused, because the job does not exist."""
    code = cli_main(["--repo", str(repo), "--state-dir", str(state_dir), "--tracker", "local",
                     *stage_argv(stage, "int-20990101-0001")])
    out, err = capsys.readouterr()
    assert code == 1 and "int-20990101-0001" in out + err and "usage:" not in err
