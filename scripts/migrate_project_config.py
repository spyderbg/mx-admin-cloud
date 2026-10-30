#!/usr/bin/env python3

"""Migrate a machine-local Worker project configuration for the test_environment relocation.

The test environment moved from scripts/ (and the repository root) into test_environment/ on
2026-10-30. A project file written before that names the old locations, and the Worker refuses
to start with it (feature_coordinator.config.RELOCATED_PATHS). This script rewrites only the
affected list entries, in place, and keeps every other line, comment and setting:

  * protected_paths, approval_required_paths and every role's excluded_paths: an old location
    becomes its test_environment/ equivalent;
  * protected_paths gains whatever it does not cover of test_environment/mxfix/**,
    test_environment/mxissues/** and test_environment/testenv.local.toml, and
    approval_required_paths gains test_environment/testenv.toml;
  * a role that owns test_environment/testenv.toml or testenv.local.toml (the automation role,
    through test_environment/**) excludes them, as the built-in configuration does.

Usage:
    python scripts/migrate_project_config.py CONFIG           # dry run: show the diff
    python scripts/migrate_project_config.py --apply CONFIG   # back up CONFIG, then rewrite it

Exit codes: 0 nothing to change (the file is already migrated) or written; 1 dry run with
changes due; 2 the file cannot be read or edited safely, or the result would not validate.
--apply first copies the file to CONFIG.bak-<UTC timestamp> with the same mode, then replaces
it atomically. A second run finds nothing to change and writes nothing.
"""

from __future__ import annotations

import argparse
import copy
import difflib
import json
import os
import re
import stat
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import yaml

from feature_coordinator.config import (RELOCATED_PATHS, TEST_ENVIRONMENT_APPROVAL_REQUIRED,
                                        TEST_ENVIRONMENT_PROTECTED, ConfigError, config_from_mapping, matches)

LOCAL_TOML = "test_environment/testenv.local.toml"
ANCHOR = "test_environment/mxissues/**"  # additions go right after it, as in the example configuration
WIDTH = 120
COMMENTS = {LOCAL_TOML: "# per-developer test-env overrides (Git-ignored)"}
EXCLUDE_NOTE = ("# testenv.toml is inside test_environment/** but approval-required; owned paths need no grant, "
                "so exclude it")
ITEM = re.compile(r"""^(?P<lead>\s*-\s+)(?P<q>["']?)(?P<value>[^\s"'#]+)(?P=q)(?P<gap>\s*)(?P<comment>#.*)?$""")
KeyPath = tuple[str, ...]


class MigrationError(Exception):
    """The file cannot be migrated automatically; change it by hand."""


# -- what must change (on the parsed YAML) ------------------------------------------------

def _covered(patterns: Iterable[str], pattern: str) -> bool:
    probe = pattern[:-2] + "probe" if pattern.endswith("/**") else pattern
    return any(matches(p, probe) for p in patterns)


def _strings(data: Any, path: KeyPath) -> list[str]:
    value = data
    for key in path:
        value = value.get(key) if isinstance(value, dict) else None
    if value is None and path[-1] == "excluded_paths":
        return []
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise MigrationError(f"{'.'.join(path)} must be a list of path strings")
    return value


def _relocated(items: list[str]) -> list[str]:
    return list(dict.fromkeys(RELOCATED_PATHS.get(item, item) for item in items))


def _with(items: list[str], additions: Iterable[str]) -> list[str]:
    new = [a for a in additions if a not in items]
    at = items.index(ANCHOR) + 1 if ANCHOR in items else len(items)
    return [*items[:at], *new, *items[at:]]


def plan(data: Any) -> dict[KeyPath, list[str]]:
    """{key path: new list} for every list that must change."""
    if not isinstance(data, dict) or not isinstance(data.get("roles"), dict):
        raise MigrationError("not a Worker project configuration (a mapping with roles)")
    changes: dict[KeyPath, list[str]] = {}
    protected = _relocated(_strings(data, ("protected_paths",)))
    protected = _with(protected, [p for p in TEST_ENVIRONMENT_PROTECTED if not _covered(protected, p)])
    approval = _relocated(_strings(data, ("approval_required_paths",)))
    toml = TEST_ENVIRONMENT_APPROVAL_REQUIRED
    if not _covered(protected, toml) and not _covered(approval, toml):
        approval.append(toml)
    lists = {("protected_paths",): protected, ("approval_required_paths",): approval}
    for name, role in data["roles"].items():
        owned = _strings(data, ("roles", name, "owned_paths"))
        excluded = _relocated(_strings(data, ("roles", name, "excluded_paths")))
        if not isinstance(role, dict):
            raise MigrationError(f"roles.{name} must be a mapping")
        lists["roles", name, "excluded_paths"] = _with(
            excluded, [p for p in (toml, LOCAL_TOML) if _covered(owned, p) and not _covered(excluded, p)])
    for path, items in lists.items():
        if items != _strings(data, path):
            changes[path] = items
    return changes


# -- applying it to the text, so every other line stays as it is ---------------------------

def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _content(line: str) -> bool:
    return bool(line.strip()) and not line.lstrip().startswith("#")


def _block_end(lines: list[str], i: int) -> int:
    """The first line after the block of the key on line *i* (trailing comments excluded)."""
    base, j = _indent(lines[i]), i + 1
    while j < len(lines) and (not _content(lines[j]) or _indent(lines[j]) > base
                              or (_indent(lines[j]) == base and lines[j].lstrip().startswith("- "))):
        j += 1
    while j - 1 > i and not _content(lines[j - 1]):
        j -= 1
    return j


def _find(lines: list[str], path: KeyPath) -> int | None:
    lo, hi, found = 0, len(lines), None
    for key in path:
        pattern, child, found = re.compile(rf"\s*{re.escape(key)}\s*:(\s|$)"), None, None
        for i in range(lo, hi):
            if _content(lines[i]):
                child = _indent(lines[i]) if child is None else child
                if _indent(lines[i]) == child and pattern.match(lines[i]):
                    found = i
                    break
        if found is None:
            return None
        lo, hi = found + 1, _block_end(lines, found)
    return found


def _flow(prefix: str, items: list[str], tail: str) -> list[str]:
    """`prefix` ends with '['; items wrap at WIDTH, aligned under the first one."""
    out, line = [], prefix
    for n, item in enumerate(items):
        token = json.dumps(item) + ("," if n < len(items) - 1 else "]")
        if line.strip() and not line.endswith("[") and len(line) + 1 + len(token) > WIDTH:
            out.append(line + "\n")
            line = " " * len(prefix)
        line += ("" if line.endswith("[") or not line.strip() else " ") + token
    return [*out, (line if items else line + "]") + tail]


def _edit_flow(lines: list[str], i: int, items: list[str]) -> None:
    end = next((k for k in range(i, len(lines)) if "]" in lines[k].split("#", 1)[0]), None)
    if end is None:
        raise MigrationError(f"line {i + 1}: unterminated flow list")
    prefix = lines[i][:lines[i].index("[") + 1]
    tail = lines[end][lines[end].index("]") + 1:]
    lines[i:end + 1] = _flow(prefix, items, tail)


def _edit_block(lines: list[str], i: int, items: list[str]) -> None:
    rows = {k: ITEM.match(lines[k].rstrip("\n")) for k in range(i + 1, _block_end(lines, i)) if _content(lines[k])}
    if not rows or not all(rows.values()):
        raise MigrationError(f"line {i + 1}: a block list with entries this script cannot parse")
    first, last = min(rows), max(rows)
    newline = "\n" if lines[last].endswith("\n") else ""
    segment: list[str] = []  # lines first..last; comment and blank lines between entries stay
    kept: list[str] = []
    anchor = None
    for k in range(first, last + 1):
        m = rows.get(k)
        if m is None:
            segment.append(lines[k])
            continue
        value = RELOCATED_PATHS.get(m["value"], m["value"])
        if value in kept:
            continue  # an old location's line that duplicates an entry already there
        gap = m["gap"]
        if m["comment"]:  # keep the comment's column where the longer path still fits
            gap = " " * max(1, len(gap) - (len(value) - len(m["value"])))
        segment.append(f"{m['lead']}{m['q']}{value}{m['q']}{gap}{m['comment'] or ''}".rstrip() + newline)
        kept.append(value)
        anchor = len(segment) if value == ANCHOR else anchor
    lead, q = rows[last]["lead"], rows[last]["q"]
    column = segment[anchor - 1].find("#") if anchor else -1
    added = []
    for value in (v for v in items if v not in kept):
        text, comment = f"{lead}{q}{value}{q}", COMMENTS.get(value, "") if column > 0 else ""
        added.append((text + " " * max(2, column - len(text)) + comment if comment else text) + newline)
    at = anchor if anchor is not None else len(segment)
    segment[at:at] = added
    if [ITEM.match(row.rstrip("\n"))["value"] for row in segment if _content(row)] != items:
        raise MigrationError(f"line {i + 1}: cannot place the new entries in this list safely")
    lines[first:last + 1] = segment


def _set(lines: list[str], path: KeyPath, items: list[str], *, note: bool) -> None:
    i = _find(lines, path)
    if i is None:  # a role without excluded_paths: add it after owned_paths
        owner = _find(lines, (*path[:-1], "owned_paths"))
        if owner is None or path[-1] != "excluded_paths":
            raise MigrationError(f"cannot find {'.'.join(path)}")
        at = _block_end(lines, owner)
        lines[at:at] = [" " * _indent(lines[owner]) + f"{path[-1]}: []\n"]
        i = at
    body = lines[i].split(":", 1)[1].split("#", 1)[0].strip()
    comment = " " * _indent(lines[i]) + EXCLUDE_NOTE + "\n"
    if note and lines[i - 1] != comment:
        lines.insert(i, comment)
        i += 1
    if body.startswith("["):
        _edit_flow(lines, i, items)
    elif body == "":
        _edit_block(lines, i, items)
    else:
        raise MigrationError(f"line {i + 1}: {'.'.join(path)} is neither a [flow] nor a block list")


def migrate(text: str) -> tuple[str, dict[KeyPath, list[str]]]:
    """The migrated text and the planned list changes ({} when there is nothing to change)."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise MigrationError(f"not valid YAML: {exc}") from exc
    changes = plan(data)
    lines = text.splitlines(keepends=True)
    toml = TEST_ENVIRONMENT_APPROVAL_REQUIRED
    for path, items in changes.items():
        added_toml = path[-1] == "excluded_paths" and toml in items and toml not in _strings(data, path)
        _set(lines, path, items, note=added_toml)
    new_text = "".join(lines)
    expected = copy.deepcopy(data)
    for path, items in changes.items():
        target = expected
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = items
    if yaml.safe_load(new_text) != expected:
        raise MigrationError("the edited text does not parse to exactly the planned change")
    try:
        config_from_mapping(expected)
    except ConfigError as exc:
        raise MigrationError(f"the {'migrated' if changes else 'current'} configuration does not validate: {exc}") \
            from exc
    return new_text, changes


# -- writing ------------------------------------------------------------------------------

def _backup(target: Path, original: bytes) -> Path:
    backup = target.with_name(f"{target.name}.bak-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}")
    fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, stat.S_IMODE(target.stat().st_mode))
    with os.fdopen(fd, "wb") as handle:
        handle.write(original)
        handle.flush()
        os.fsync(handle.fileno())
    return backup


def _replace(target: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            os.fchmod(handle.fileno(), stat.S_IMODE(target.stat().st_mode))
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("config", type=Path, help="the machine-local project configuration (YAML)")
    parser.add_argument("--apply", action="store_true", help="back up the file, then write the migrated version")
    args = parser.parse_args(argv)
    target = args.config.expanduser().resolve()  # a symlinked file is rewritten, the link is kept
    try:
        original = target.read_bytes()
        text = original.decode("utf-8")
        new_text, changes = migrate(text)
    except (OSError, UnicodeDecodeError, MigrationError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if not changes:
        print(f"{target}: already migrated, nothing to change")
        return 0
    sys.stdout.writelines(difflib.unified_diff(text.splitlines(True), new_text.splitlines(True),
                                               str(target), f"{target} (migrated)"))
    if not args.apply:
        print("dry run: nothing written; pass --apply to back up and rewrite the file")
        return 1
    try:
        backup = _backup(target, original)
        _replace(target, new_text)
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"backup:  {backup}\nwritten: {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
