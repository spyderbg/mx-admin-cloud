"""Release tests use disposable repositories and a local bare origin only."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "increment_version.py"
SPEC = importlib.util.spec_from_file_location("increment_version", SCRIPT)
release = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = release
SPEC.loader.exec_module(release)


class VersionTests(unittest.TestCase):
    def test_single_digit_increment_and_carry(self):
        cases = [("1.0.0", "build", "1.0.1"), ("1.0.9", "build", "1.1.0"),
                 ("1.9.9", "build", "2.0.0"), ("0.0.9", "build", "0.1.0"),
                 ("1.2.8", "minor", "1.3.0"), ("1.9.8", "minor", "2.0.0"),
                 ("1.2.8", "major", "2.0.0"), ("9.9.8", "build", "9.9.9")]
        for old, part, expected in cases:
            with self.subTest(old=old, part=part):
                self.assertEqual(release.increment(old, part), expected)

    def test_invalid_versions_and_overflow(self):
        for version in ("10.0.0", "1.10.0", "1.0.10", "01.0.0", "v1.0.0", "1.0", "1.0.0-beta", "9.9.9"):
            with self.subTest(version=version), self.assertRaises(release.ReleaseError):
                release.increment(version)
        for part in ("minor", "major"):
            with self.subTest(part=part), self.assertRaises(release.ReleaseError):
                release.increment("9.9.0", part)

    def test_subject_grouping_deduplication_and_bookkeeping(self):
        groups = release.change_entries([
            "feat(voices): Add a new voice", "fix: Repair playback", "Remove obsolete control",
            "Deprecated old endpoint", "security: Patch a vulnerability", "refactor!: Replace storage",
            "Resource changes", "Merge duplicate settings into one control",
            "feat(voices): Add a new voice", "Merge branch 'main' into dev",
            "Increment version to 1.0.0", "fix: Display <script> as text", "fix: Address CVE-2026-1234",
        ])
        self.assertEqual(groups["Added"], ["- Add a new voice"])
        self.assertEqual(groups["Removed"], ["- Remove obsolete control"])
        self.assertEqual(groups["Deprecated"], ["- Deprecated old endpoint"])
        self.assertEqual(groups["Security"], ["- Patch a vulnerability", "- Address CVE-2026-1234"])
        self.assertIn("- Breaking change: Replace storage", groups["Changed"])
        self.assertIn(r"- Display \<script\> as text", groups["Fixed"])
        self.assertNotIn("Merge branch", str(groups))
        self.assertIn("- Merge duplicate settings into one control", groups["Changed"])

    def test_changelog_preserves_curated_entries_and_history(self):
        original = ("# Project changes\n\nIntroduction.\n\n## [Unreleased]\n\n### Added\n\n"
                    "- Curated feature.\n\n## [1.0.0] - 2026-10-01\n\n### Fixed\n\n- Older fix.\n\n"
                    "[Unreleased]: https://github.com/example/app/compare/1.0.0...HEAD\n"
                    "[1.0.0]: https://github.com/example/app/commits/1.0.0\n")
        text = release.render_changelog(original, "1.0.1", "2026-10-20",
                                        release.change_entries(["fix: New fix"]),
                                        "git@github.com:example/app.git", "abc123")
        self.assertLess(text.index("## [Unreleased]"), text.index("## [1.0.1] - 2026-10-20"))
        self.assertLess(text.index("## [1.0.1]"), text.index("## [1.0.0]"))
        self.assertIn("### Added\n\n- Curated feature.", text)
        self.assertIn("### Fixed\n\n- New fix", text)
        self.assertIn("- Older fix.", text)
        self.assertIn("compare/abc123...1.0.1", text)
        self.assertEqual(text.lower().count("[unreleased]:"), 1)
        self.assertNotIn("### Removed", text)

    def test_remote_links_remove_credentials_and_support_gitlab(self):
        self.assertEqual(release.web_url("https://user:secret@gitlab.com/team/app.git"),
                         "https://gitlab.com/team/app")
        text = release.render_changelog("", "1.0.1", "2026-10-20",
                                        release.change_entries(["Added feature"]),
                                        "git@gitlab.com:team/app.git", "abc")
        self.assertIn("https://gitlab.com/team/app/-/compare/abc...1.0.1", text)


class ReleaseIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mx-release-test-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.origin = self.base / "origin.git"
        self.root = self.base / "work"
        self.run_git(self.base, "init", "--bare", str(self.origin))
        self.run_git(self.base, "init", "-b", "main", str(self.root))
        self.run_git(self.root, "config", "user.email", "release-test@example.invalid")
        self.run_git(self.root, "config", "user.name", "Release Test")
        self.run_git(self.root, "config", "commit.gpgsign", "false")
        self.run_git(self.root, "config", "tag.gpgsign", "false")
        self.run_git(self.root, "config", "core.hooksPath", str(self.base / "no-hooks"))
        self.write(release.PACKAGE, json.dumps({"name": "app", "version": "1.0.0"}, indent=2) + "\n")
        self.write(release.LOCKFILE, json.dumps({"version": "1.0.0", "lockfileVersion": 3,
                   "packages": {"": {"version": "1.0.0"}, "node_modules/example": {"version": "1.0.0"}}}, indent=2) + "\n")
        self.write(release.CHANGELOG, "")
        self.write("conflict.txt", "original\n")
        self.write("scripts/increment_version.py", SCRIPT.read_text(encoding="utf-8"))
        self.commit("Initial app")
        self.run_git(self.root, "remote", "add", "origin", str(self.origin))
        self.run_git(self.root, "push", "-u", "origin", "main")
        self.run_git(self.root, "checkout", "-b", "dev")
        self.write("feature.txt", "voices\n")
        self.commit("feat: Add voices")
        self.run_git(self.root, "push", "-u", "origin", "dev")
        self.git = release.Git(self.root)

    @staticmethod
    def run_git(root, *args):
        return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=True).stdout.strip()

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def commit(self, message):
        self.run_git(self.root, "add", "--all")
        self.run_git(self.root, "commit", "-m", message)

    def plan(self):
        return release.plan_release(self.git, "build")

    def publish(self):
        plan = self.plan()
        release.publish(self.git, plan)
        return plan

    def test_complete_release_updates_files_commits_tags_pushes_and_returns_to_dev(self):
        plan = self.publish()
        self.assertEqual(plan.version, "1.0.1")
        package = json.loads((self.root / release.PACKAGE).read_text())
        lock = json.loads((self.root / release.LOCKFILE).read_text())
        self.assertEqual(package["version"], "1.0.1")
        self.assertEqual(lock["version"], "1.0.1")
        self.assertEqual(lock["packages"][""]["version"], "1.0.1")
        self.assertEqual(lock["packages"]["node_modules/example"]["version"], "1.0.0")
        self.assertEqual(self.git.read("log", "-1", "--format=%s"), "Increment version to 1.0.1")
        head = self.git.read("rev-parse", "HEAD")
        for ref in ("refs/heads/dev", "refs/heads/main", "refs/tags/1.0.1^{commit}"):
            self.assertEqual(self.run_git(self.origin, "rev-parse", ref), head)
        self.assertEqual(self.git.read("cat-file", "-t", "1.0.1"), "tag")
        self.assertEqual(self.git.read("branch", "--show-current"), "dev")
        self.assertFalse(self.git.read("status", "--porcelain"))

    def test_only_new_commit_subjects_are_included_in_next_release(self):
        self.publish()
        with self.assertRaisesRegex(release.ReleaseError, "No changes"):
            self.plan()
        self.write("feature.txt", "better playback\n")
        self.commit("fix: Keep playback working\n\nBody must not enter the changelog.")
        self.run_git(self.root, "push", "origin", "dev")
        plan = self.plan()
        self.assertEqual(plan.version, "1.0.2")
        self.assertEqual(plan.subjects, ["fix: Keep playback working"])
        self.assertNotIn("Body must not", plan.files[release.CHANGELOG])
        self.assertEqual(plan.files[release.CHANGELOG].count("- Add voices"), 1)

    def test_dirty_staged_unstaged_and_untracked_files_are_rejected(self):
        for mode in ("unstaged", "staged", "untracked"):
            with self.subTest(mode=mode):
                path = "extra.txt" if mode == "untracked" else "feature.txt"
                self.write(path, "uncommitted\n")
                if mode == "staged":
                    self.run_git(self.root, "add", path)
                with self.assertRaisesRegex(release.ReleaseError, "Working tree must be clean"):
                    self.plan()
                if mode == "untracked":
                    (self.root / path).unlink()
                else:
                    self.run_git(self.root, "reset", "--hard", "HEAD")

    def test_wrong_branch_and_detached_head_are_rejected(self):
        self.run_git(self.root, "checkout", "main")
        with self.assertRaisesRegex(release.ReleaseError, "current branch is 'main'"):
            self.plan()
        self.run_git(self.root, "checkout", "--detach", "dev")
        with self.assertRaisesRegex(release.ReleaseError, "detached HEAD"):
            self.plan()

    def test_ahead_behind_and_divergent_dev_are_rejected(self):
        baseline = self.git.read("rev-parse", "HEAD")
        self.write("extra.txt", "local change")
        self.commit("Local change")
        with self.assertRaisesRegex(release.ReleaseError, "1 ahead, 0 behind"):
            self.plan()
        self.run_git(self.root, "push", "origin", "dev")
        self.run_git(self.root, "reset", "--hard", baseline)
        with self.assertRaisesRegex(release.ReleaseError, "0 ahead, 1 behind"):
            self.plan()
        self.write("different.txt", "different local change")
        self.commit("Different local change")
        with self.assertRaisesRegex(release.ReleaseError, "1 ahead, 1 behind"):
            self.plan()

    def test_local_main_with_unpushed_commits_is_rejected(self):
        self.run_git(self.root, "checkout", "main")
        self.write("local-main.txt", "local main change")
        self.commit("Unpushed main commit")
        self.run_git(self.root, "checkout", "dev")
        with self.assertRaisesRegex(release.ReleaseError, "Local 'main' has unpushed"):
            self.plan()

    def test_missing_local_main_is_created_from_origin(self):
        self.run_git(self.root, "branch", "-D", "main")
        self.publish()
        self.assertTrue(self.git.exists("refs/heads/main"))

    def test_main_checked_out_elsewhere_is_rejected(self):
        self.run_git(self.root, "worktree", "add", str(self.base / "other-worktree"), "main")
        with self.assertRaisesRegex(release.ReleaseError, "another worktree"):
            self.plan()

    def test_remote_tag_collision_is_rejected_without_writing_files(self):
        self.run_git(self.root, "tag", "1.0.1")
        self.run_git(self.root, "push", "origin", "refs/tags/1.0.1")
        self.run_git(self.root, "tag", "-d", "1.0.1")
        with self.assertRaisesRegex(release.ReleaseError, "Tag 1.0.1 already exists"):
            self.plan()
        self.assertFalse(self.git.read("status", "--porcelain"))

    def test_lockfile_mismatch_is_rejected(self):
        lock = json.loads((self.root / release.LOCKFILE).read_text())
        lock["version"] = "0.1.0"
        self.write(release.LOCKFILE, json.dumps(lock))
        self.commit("Mismatched package versions")
        self.run_git(self.root, "push", "origin", "dev")
        with self.assertRaisesRegex(release.ReleaseError, "root versions must match"):
            self.plan()

    def test_existing_version_tag_is_used_as_initial_boundary(self):
        self.run_git(self.root, "tag", "1.0.0", "main")
        plan = self.plan()
        self.assertEqual(plan.subjects, ["feat: Add voices"])

    def test_merge_conflict_aborts_merge_returns_to_dev_and_reports_partial_release(self):
        self.write("conflict.txt", "dev change\n")
        self.commit("fix: Dev change")
        self.run_git(self.root, "push", "origin", "dev")
        self.run_git(self.root, "checkout", "main")
        self.write("conflict.txt", "main change\n")
        self.commit("Main change")
        self.run_git(self.root, "push", "origin", "main")
        old_main = self.git.read("rev-parse", "main")
        self.run_git(self.root, "checkout", "dev")
        with self.assertRaisesRegex(release.ReleaseError, "origin/dev and tag pushed"):
            self.publish()
        self.assertEqual(self.git.read("branch", "--show-current"), "dev")
        self.assertFalse(self.git.exists("MERGE_HEAD"))
        self.assertFalse(self.git.read("status", "--porcelain"))
        self.assertEqual(self.run_git(self.origin, "rev-parse", "main"), old_main)

    def test_push_failure_does_not_touch_main_and_reports_completed_work(self):
        original_command = self.git.command
        def reject_push(*args, **kwargs):
            if args[0] == "push":
                raise release.ReleaseError("Push rejected by test origin")
            return original_command(*args, **kwargs)
        plan = self.plan()
        old_main = self.git.read("rev-parse", "main")
        with patch.object(self.git, "command", side_effect=reject_push):
            with self.assertRaisesRegex(release.ReleaseError, "local tag 1.0.1"):
                release.publish(self.git, plan)
        self.assertEqual(self.git.read("branch", "--show-current"), "dev")
        self.assertEqual(self.run_git(self.origin, "rev-parse", "main"), old_main)
        self.assertNotIn("1.0.1", self.run_git(self.origin, "tag", "--list"))

    def test_atomic_push_rejects_dev_update_when_remote_tag_appears_during_release(self):
        plan = self.plan()
        before = self.run_git(self.origin, "rev-parse", "dev")
        self.run_git(self.origin, "tag", "1.0.1", "main")
        with self.assertRaisesRegex(release.ReleaseError, "pushing dev and its tag"):
            release.publish(self.git, plan)
        self.assertEqual(self.run_git(self.origin, "rev-parse", "dev"), before)
        self.assertEqual(self.run_git(self.origin, "rev-parse", "1.0.1"),
                         self.run_git(self.origin, "rev-parse", "main"))

    def test_failed_main_push_returns_to_dev_and_reports_dev_was_published(self):
        original_command = self.git.command
        def reject_main_push(*args, **kwargs):
            if args[0] == "push" and "HEAD:refs/heads/main" in args:
                raise release.ReleaseError("Main is protected")
            return original_command(*args, **kwargs)
        plan = self.plan()
        old_main = self.git.read("rev-parse", "main")
        with patch.object(self.git, "command", side_effect=reject_main_push):
            with self.assertRaisesRegex(release.ReleaseError, "origin/dev and tag pushed"):
                release.publish(self.git, plan)
        self.assertEqual(self.git.read("branch", "--show-current"), "dev")
        self.assertEqual(self.run_git(self.origin, "rev-parse", "main"), old_main)
        self.assertEqual(self.run_git(self.origin, "rev-parse", "1.0.1^{commit}"),
                         self.run_git(self.origin, "rev-parse", "dev"))

    def test_cli_publishes_requested_minor_version(self):
        result = subprocess.run([sys.executable, str(self.root / "scripts/increment_version.py"),
                                 "--part", "minor"], cwd=self.root / "frontend", text=True,
                                capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Released 1.1.0", result.stdout)
        self.assertEqual(self.run_git(self.origin, "rev-parse", "1.1.0^{commit}"),
                         self.run_git(self.origin, "rev-parse", "main"))

    def test_branch_switch_after_planning_is_rejected_before_writing(self):
        plan = self.plan()
        self.run_git(self.root, "branch", "-f", "main", "dev")
        self.run_git(self.root, "checkout", "main")
        with self.assertRaisesRegex(release.ReleaseError, "Current branch changed"):
            release.publish(self.git, plan)
        self.assertFalse(self.git.read("status", "--porcelain"))
        self.assertFalse(self.git.exists("refs/tags/1.0.1"))

    def test_cli_dry_run_fetches_and_previews_without_changing_repository(self):
        before = self.git.read("rev-parse", "HEAD")
        result = subprocess.run([sys.executable, str(self.root / "scripts/increment_version.py"), "--dry-run"],
                                cwd=self.root / "frontend", text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Version: 1.0.1", result.stdout)
        self.assertIn("### Added", result.stdout)
        self.assertEqual(self.git.read("rev-parse", "HEAD"), before)
        self.assertFalse(self.git.read("status", "--porcelain"))
        self.assertFalse(self.git.exists("refs/tags/1.0.1"))


if __name__ == "__main__":
    unittest.main()
