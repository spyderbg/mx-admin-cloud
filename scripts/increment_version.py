#!/usr/bin/env python3

"""Increment the app version, publish dev and its tag, and merge dev into main.

Run --dry-run first to review the changelog. Requires Python 3.10+ and Git;
uses only the standard library. See docs/version_releases.md for the workflow.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, urlsplit
from zoneinfo import ZoneInfo

PACKAGE = "frontend/package.json"
LOCKFILE = "frontend/package-lock.json"
CHANGELOG = "docs/log_changes.md"
CATEGORIES = ("Added", "Changed", "Deprecated", "Removed", "Fixed", "Security")
VERSION = re.compile(r"[0-9]\.[0-9]\.[0-9]\Z")
RELEASE_SUBJECT = re.compile(r"Increment version to ([0-9]\.[0-9]\.[0-9])\Z")


class ReleaseError(RuntimeError):
    """An actionable release validation or Git error."""


def release_tag(version: str) -> str:
    return f"releases/v{version}"


class Git:
    def __init__(self, root: Path):
        self.root = root

    def command(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        try:
            result = subprocess.run(
                ["git", *args], cwd=self.root, text=True, encoding="utf-8",
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120,
                env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"},
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ReleaseError(f"Could not run git {args[0]}: {exc}") from exc
        if check and result.returncode:
            reason = result.stderr.strip() or result.stdout.strip() or "command failed"
            raise ReleaseError(f"git {' '.join(args)}: {reason}")
        return result

    def read(self, *args: str) -> str:
        return self.command(*args).stdout.strip()

    def exists(self, ref: str) -> bool:
        return self.command("rev-parse", "--verify", "--quiet", ref, check=False).returncode == 0

    def ancestor(self, older: str, newer: str) -> bool:
        result = self.command("merge-base", "--is-ancestor", older, newer, check=False)
        if result.returncode not in (0, 1):
            raise ReleaseError(result.stderr.strip())
        return result.returncode == 0


def increment(version: str, part: str = "build") -> str:
    """Carry digits in base ten, resetting the less significant components."""
    if not VERSION.fullmatch(version):
        raise ReleaseError(f"Invalid version {version!r}; expected three single digits (e.g. 1.0.0).")
    major, minor, build = map(int, version.split("."))
    number = major * 100 + minor * 10 + build
    if part == "build":
        number += 1
    elif part == "minor":
        number = (number // 10 + 1) * 10
    elif part == "major":
        number = (major + 1) * 100
    else:
        raise ReleaseError(f"Unknown version component: {part}")
    if number > 999:
        raise ReleaseError(f"Cannot increment {version}: the single-digit version limit is 9.9.9.")
    return f"{number // 100}.{number // 10 % 10}.{number % 10}"


def require_clean(git: Git) -> None:
    status = git.read("status", "--porcelain=v1", "--untracked-files=all", "--ignore-submodules=none")
    if status:
        raise ReleaseError("Working tree must be clean: staged, unstaged, or untracked changes exist:\n" + status)
    for name in ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-merge", "rebase-apply", "sequencer"):
        git_path = Path(git.read("rev-parse", "--git-path", name))
        if not git_path.is_absolute():
            git_path = git.root / git_path
        if git_path.exists():
            raise ReleaseError(f"Finish the Git operation in progress ({name}) before releasing.")


def preflight(git: Git) -> str:
    require_clean(git)
    branch = git.command("symbolic-ref", "--quiet", "--short", "HEAD", check=False)
    if branch.returncode or branch.stdout.strip() != "dev":
        actual = branch.stdout.strip() or "detached HEAD"
        raise ReleaseError(f"Release requires branch 'dev'; current branch is '{actual}'.")
    if git.command("remote", "get-url", "origin", check=False).returncode:
        raise ReleaseError("Remote 'origin' is not configured.")
    # Explicit refspecs also work with a single-branch clone's fetch configuration.
    git.command("fetch", "--prune", "--tags", "origin",
                "+refs/heads/dev:refs/remotes/origin/dev",
                "+refs/heads/main:refs/remotes/origin/main")
    head = git.read("rev-parse", "HEAD")
    if head != git.read("rev-parse", "refs/remotes/origin/dev"):
        ahead, behind = git.read("rev-list", "--left-right", "--count", "HEAD...origin/dev").split()
        raise ReleaseError(f"Branch 'dev' is not up to date with origin/dev ({ahead} ahead, {behind} behind). "
                           "Synchronize it before releasing.")
    if git.exists("refs/heads/main") and not git.ancestor("main", "origin/main"):
        raise ReleaseError("Local 'main' has unpushed or divergent commits; synchronize it with origin/main first.")
    if "branch refs/heads/main" in git.read("worktree", "list", "--porcelain").splitlines():
        raise ReleaseError("Branch 'main' is checked out in another worktree; release from a checkout where main is available.")
    return head


def release_boundary(git: Git, version: str) -> str | None:
    for line in git.read("log", "--first-parent", "--format=%H%x09%s").splitlines():
        commit, subject = line.split("\t", 1)
        match = RELEASE_SUBJECT.fullmatch(subject)
        if match:
            if match[1] != version:
                raise ReleaseError(f"Package version {version} differs from the last increment ({match[1]}).")
            for tag in (release_tag(version), version, f"v{version}"):
                ref = f"refs/tags/{tag}"
                if git.exists(ref) and git.read("rev-parse", f"{ref}^{{commit}}") != commit:
                    raise ReleaseError(f"Tag {tag} does not point to the last version increment commit.")
            return commit
    # Bootstrap repositories with a version tag but no script-generated commit.
    for tag in (release_tag(version), version, f"v{version}"):
        if git.exists(f"refs/tags/{tag}") and git.ancestor(f"refs/tags/{tag}^{{commit}}", "HEAD"):
            return git.read("rev-parse", f"refs/tags/{tag}^{{commit}}")
    return None


def change_entries(subjects: list[str]) -> dict[str, list[str]]:
    """Group commit subjects; merge/release bookkeeping is not a user change."""
    groups: dict[str, list[str]] = {name: [] for name in CATEGORIES}
    for subject in subjects:
        if RELEASE_SUBJECT.fullmatch(subject) or re.match(
            r"^(?:Merge (?:branch|pull request|remote-tracking branch|tag|commit)\b|Merged (?:in|PR)\b)",
            subject, re.I,
        ):
            continue
        conventional = re.match(r"([a-z]+)(?:\([^)]*\))?(!)?:\s*(.+)", subject, re.I)
        kind = conventional[1].lower() if conventional else subject.split(maxsplit=1)[0].lower()
        text = conventional[3] if conventional else subject
        if re.search(r"\b(?:security|CVE-\d+(?:-\d+)?|vulnerabilit\w*|XSS|CSRF)\b", subject, re.I):
            category = "Security"
        elif kind in ("feat", "feature", "add", "adds", "added"):
            category = "Added"
        elif kind in ("fix", "fixes", "fixed", "bugfix", "hotfix"):
            category = "Fixed"
        elif kind.startswith("deprecat"):
            category = "Deprecated"
        elif kind in ("remove", "removes", "removed", "delete", "deletes", "deleted"):
            category = "Removed"
        else:
            category = "Changed"
        if conventional and conventional[2]:
            text = "Breaking change: " + text
        # Subjects remain text, including when they contain HTML or Markdown.
        text = re.sub(r"([\\`*_{}\[\]<>])", r"\\\1", text)
        entry = "- " + text
        if entry not in groups[category]:
            groups[category].append(entry)
    return groups


def web_url(remote: str) -> str | None:
    if re.match(r"[^/@:]+@[^/:]+:", remote):
        remote = "https://" + remote.split("@", 1)[1].replace(":", "/", 1)
    parsed = urlsplit(remote)
    if parsed.scheme not in ("https", "http", "ssh") or not parsed.hostname:
        return None
    scheme = "https" if parsed.scheme == "ssh" else parsed.scheme
    host = parsed.hostname
    if parsed.port and parsed.scheme != "ssh":
        host += f":{parsed.port}"
    return f"{scheme}://{host}{parsed.path.removesuffix('.git').rstrip('/')}"


def render_changelog(original: str, version: str, date: str, groups: dict[str, list[str]],
                     remote: str, boundary: str | None) -> str:
    if re.search(rf"^## (?:\[{re.escape(version)}\]|{re.escape(version)})(?:\s|$)", original, re.M):
        raise ReleaseError(f"Changelog already contains version {version}.")
    headings = list(re.finditer(r"^## (?!#).+$", original, re.M))
    intro = original[:headings[0].start()].rstrip() if headings else original.rstrip()
    history = original[headings[0].start():].strip() if headings else ""
    manual = ""
    if headings and headings[0][0].strip() in ("## [Unreleased]", "## Unreleased"):
        end = headings[1].start() if len(headings) > 1 else len(original)
        manual = original[headings[0].end():end].strip()
        history = original[end:].strip()
    if not intro:
        intro = ("# Changelog\n\nAll notable changes to this project will be documented in this file.\n\n"
                 "The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).\n"
                 "Versions follow the project's single-digit major.minor.build scheme.")
    extras: list[str] = []
    parts = re.split(r"^### ([^\n]+)\s*$", manual, flags=re.M)
    if parts[0].strip():
        extras.append(parts[0].strip())
    for index in range(1, len(parts), 2):
        name, body = parts[index].strip(), parts[index + 1].strip()
        if name in groups:
            groups[name] = body.splitlines() + [entry for entry in groups[name] if entry not in body.splitlines()]
        elif body:
            extras.append(f"### {name}\n\n{body}")
    sections = extras + [f"### {name}\n\n" + "\n".join(groups[name])
                         for name in CATEGORIES if any(line.strip() for line in groups[name])]
    url = web_url(remote)
    unreleased = "[Unreleased]" if url else "Unreleased"
    title = f"[{version}]" if url else version
    result = f"{intro}\n\n## {unreleased}\n\n## {title} - {date}\n\n" + "\n\n".join(sections)
    if history:
        result += "\n\n" + history
    if url:
        # Preserve previous release links; replace only the moving Unreleased link.
        result = re.sub(r"^\[unreleased\]:.*\n?", "", result, flags=re.M | re.I).rstrip()
        separator = "/-/" if "gitlab" in urlsplit(url).hostname else "/"
        compare = f"{url}{separator}compare/"
        tag_url = quote(release_tag(version), safe="")
        release_url = f"{compare}{boundary}...{tag_url}" if boundary else f"{url}{separator}commits/{tag_url}"
        result += f"\n\n[Unreleased]: {compare}{tag_url}...HEAD\n[{version}]: {release_url}"
    return result.rstrip() + "\n"


@dataclass
class ReleasePlan:
    head: str
    version: str
    files: dict[str, str]
    subjects: list[str]


def plan_release(git: Git, part: str) -> ReleasePlan:
    head = preflight(git)
    try:
        package = json.loads((git.root / PACKAGE).read_text(encoding="utf-8"))
        lock = json.loads((git.root / LOCKFILE).read_text(encoding="utf-8"))
        current = package["version"]
        if not isinstance(current, str):
            raise ReleaseError("Package version must be a string.")
        version = increment(current, part)
        if lock["version"] != current or lock["packages"][""]["version"] != current:
            raise ReleaseError("package.json and package-lock.json root versions must match before releasing.")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ReleaseError(f"Cannot read frontend version files: {exc}") from exc
    tag = release_tag(version)
    if git.exists(f"refs/tags/{tag}"):
        raise ReleaseError(f"Tag {tag} already exists; refusing to overwrite a release.")
    boundary = release_boundary(git, current)
    revision = f"{boundary}..{head}" if boundary else head
    subjects = git.read("log", "--reverse", "--format=%s", revision).splitlines()
    groups = change_entries(subjects)
    if not any(groups.values()):
        raise ReleaseError("No changes found since the last version increment.")
    changelog_path = git.root / CHANGELOG
    original = changelog_path.read_text(encoding="utf-8") if changelog_path.exists() else ""
    changelog = render_changelog(
        original, version, datetime.now(ZoneInfo("Europe/Sofia")).date().isoformat(),
        groups, git.read("remote", "get-url", "origin"), boundary,
    )
    package["version"] = lock["version"] = lock["packages"][""]["version"] = version
    return ReleasePlan(head, version, {
        PACKAGE: json.dumps(package, indent=2, ensure_ascii=False) + "\n",
        LOCKFILE: json.dumps(lock, indent=2, ensure_ascii=False) + "\n",
        CHANGELOG: changelog,
    }, subjects)


def publish(git: Git, plan: ReleasePlan) -> None:
    require_clean(git)
    branch = git.command("symbolic-ref", "--quiet", "--short", "HEAD", check=False)
    if branch.returncode or branch.stdout.strip() != "dev":
        raise ReleaseError("Current branch changed during release preparation; return to dev and run again.")
    if git.read("rev-parse", "HEAD") != plan.head:
        raise ReleaseError("HEAD changed during release preparation; run the script again.")
    phase = "writing release files"
    completed: list[str] = []
    switched = False
    merge_started = False
    try:
        for name, content in plan.files.items():
            path = git.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        phase = "committing the release"
        # Only release files enter the index; never stage unrelated user files.
        git.command("add", "--", *plan.files)
        git.command("commit", "--only", "-m", f"Increment version to {plan.version}", "--", *plan.files)
        completed.append("release commit")
        require_clean(git)
        phase = "creating the version tag"
        tag = release_tag(plan.version)
        git.command("tag", "-a", tag, "-m", f"Increment version to {plan.version}")
        completed.append(f"local tag {tag}")
        phase = "pushing dev and its tag"
        git.command("push", "--atomic", "origin", "HEAD:refs/heads/dev",
                    f"refs/tags/{tag}:refs/tags/{tag}")
        completed.append("origin/dev and tag pushed")
        phase = "checking out main"
        if git.exists("refs/heads/main"):
            git.command("checkout", "main")
        else:
            git.command("checkout", "-b", "main", "--track", "origin/main")
        switched = True
        phase = "updating main"
        git.command("pull", "--ff-only", "origin", "main")
        phase = "merging dev into main"
        merge_started = True
        git.command("merge", "--no-edit", "dev")
        merge_started = False
        phase = "pushing main"
        git.command("push", "origin", "HEAD:refs/heads/main")
        completed.append("origin/main released")
    except ReleaseError as exc:
        if merge_started and git.exists("MERGE_HEAD"):
            git.command("merge", "--abort")
        done = ", ".join(completed) or "none"
        raise ReleaseError(f"Release stopped while {phase}. Completed: {done}.\n{exc}\n"
                           "Inspect Git state before retrying; published commits and tags are preserved.") from exc
    finally:
        if switched:
            git.command("checkout", "dev")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--part", choices=("build", "minor", "major"), default="build",
                        help="component to increment (default: build); digits carry at 9")
    parser.add_argument("--dry-run", action="store_true",
                        help="fetch and validate, then preview without writing, committing, tagging, or pushing")
    args = parser.parse_args(argv)
    try:
        git = Git(Path(__file__).resolve().parent.parent)
        plan = plan_release(git, args.part)
        print(f"Version: {plan.version}; tag: {release_tag(plan.version)}; collected {len(plan.subjects)} commit subjects.")
        if args.dry_run:
            print(f"\n--- Preview: {CHANGELOG} ---\n{plan.files[CHANGELOG]}")
            print("Dry run: release files and branches are unchanged (origin refs were fetched).")
        else:
            publish(git, plan)
            print(f"Released {plan.version} to origin/dev and origin/main; returned to dev.")
        return 0
    except (ReleaseError, OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
