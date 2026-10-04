#!/usr/bin/env python3
"""Build the throwaway `.testenv/` content fixture from the real `content/` directory.

Copies a curated 4-category subset (at_school, drinks, at_home, fruits) out of the
real content/ tree, slicing only the row-aligned index CSVs; every other file —
images, MP3s, voice sidecars, stray legacy files — is copied byte-for-byte. Nothing
under .testenv/ is ever a symlink into content/ or backend/config/: the suite includes
destructive specs (archive, delete item, delete image) that would follow a symlink
straight into tracked, real content.

See docs/test_environment/implementation_plan.md §3 for the fixture design rationale.

Usage:
    python scripts/testenv_seed.py              # build (or rebuild empty) .testenv/
    python scripts/testenv_seed.py --force       # wipe an existing .testenv/content and rebuild
    python scripts/testenv_seed.py --check-ts    # compare real content/ against the stored
                                                  # manifest without reseeding; nonzero on drift
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.stdout.reconfigure(line_buffering=True)  # see testenv.py for why

from testenvlib import paths  # noqa: E402
from testenvlib.envfile import write_env_file  # noqa: E402

RED = "\033[91m"
YELLOW = "\033[93m"
GREEN = "\033[92m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"

_UNDERSCORE_RE = re.compile(r"_+")
_CAMEL_BOUNDARY_RE = re.compile(r"(?<!^)(?=[A-Z])")


def _folder_name_from_item_id(item_id: str) -> str:
    """Mirror ContentProvider._folder_name_from_item_id exactly ('I_WaterBottle' -> 'water_bottle').

    Duplicated rather than imported: importing backend/src from scripts/ would require
    putting the backend package on sys.path just for a few-line pure string transform,
    coupling the seed tooling to backend internals for no real benefit. If this rule
    ever changes, content-quirks.spec.ts / item tests will fail loudly against the app
    itself, not silently here.
    """
    _, _, remainder = item_id.partition("_")
    folder = _CAMEL_BOUNDARY_RE.sub("_", remainder or item_id).lower()
    return _UNDERSCORE_RE.sub("_", folder).strip("_")


def _read_csv_rows(path: Path) -> list[list[str]]:
    with path.open(newline="", encoding="utf-8") as fh:
        return [row for row in csv.reader(fh)]


def _write_csv_rows(path: Path, rows: list[list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerows(rows)


def _slice_rows(rows: list[list[str]], indices: tuple[int, ...]) -> list[list[str]]:
    sliced: list[list[str]] = []
    for i in indices:
        sliced.append(rows[i] if i < len(rows) else [""])
    return sliced


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# Manifest — drift detection against the real content/ this fixture was cut from
# ---------------------------------------------------------------------------

def _collect_source_files() -> dict[str, dict[str, object]]:
    """Return {relpath-from-content/: {size, sha256}} for every file this seed reads."""
    files: dict[str, dict[str, object]] = {}

    for filename in paths.ROOT_SLICED_CSV_FILES:
        src = paths.REAL_CONTENT_DIR / filename
        if src.is_file():
            files[filename] = {"size": src.stat().st_size, "sha256": _sha256_file(src)}

    for _, folder in paths.KEPT_CATEGORY_ROWS:
        category_src = paths.REAL_CONTENT_DIR / folder
        if not category_src.is_dir():
            raise FileNotFoundError(f"Expected source category directory missing: {category_src}")

        kept_item_folders = _kept_item_folder_names(category_src, folder)
        for entry in sorted(category_src.rglob("*")):
            if not entry.is_file():
                continue
            rel_to_category = entry.relative_to(category_src)
            top = rel_to_category.parts[0]
            if entry.parent != category_src and top not in kept_item_folders:
                continue  # inside a dropped item folder (e.g. calculator/, bananas/)
            rel = f"{folder}/{rel_to_category.as_posix()}"
            files[rel] = {"size": entry.stat().st_size, "sha256": _sha256_file(entry)}

    return files


def _kept_item_folder_names(category_src: Path, folder: str) -> set[str]:
    indices = paths.KEPT_ITEM_ROWS.get(folder)
    if indices is None:
        return set()
    ids_path = category_src / paths.ITEMS_IDS_FILE
    if not ids_path.is_file():
        return set()
    id_rows = _read_csv_rows(ids_path)
    names: set[str] = set()
    for i in indices:
        if i >= len(id_rows) or not id_rows[i]:
            continue
        folder_name = _folder_name_from_item_id(id_rows[i][0].strip())
        if folder_name:
            names.add(folder_name)
    return names


def build_manifest(kept_files: dict[str, dict[str, object]]) -> dict[str, object]:
    aggregate = hashlib.sha256()
    for rel in sorted(kept_files):
        if Path(rel).name in paths.ROOT_SLICED_CSV_FILES or Path(rel).name in paths.ITEM_SLICED_CSV_FILES:
            aggregate.update(rel.encode("utf-8"))
            aggregate.update(str(kept_files[rel]["sha256"]).encode("utf-8"))
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_content_dir": str(paths.REAL_CONTENT_DIR),
        "categories": [folder for _, folder in paths.KEPT_CATEGORY_ROWS],
        "kept_category_rows": [[i, folder] for i, folder in paths.KEPT_CATEGORY_ROWS],
        "kept_item_rows": {k: list(v) for k, v in paths.KEPT_ITEM_ROWS.items()},
        "sliced_csv_digest": aggregate.hexdigest(),
        "files": kept_files,
    }


def check_timestamps() -> int:
    """`--check-ts`: report drift between real content/ and the last-recorded manifest."""
    if not paths.SEED_MANIFEST_PATH.is_file():
        print(
            f"{YELLOW}No manifest at {paths.testenv_relative(paths.SEED_MANIFEST_PATH)} — "
            f"run `python scripts/testenv_seed.py` first.{RESET}",
        )
        return 1

    try:
        stored = json.loads(paths.SEED_MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"{RED}Could not read existing manifest: {exc}{RESET}", file=sys.stderr)
        return 1

    current_files = _collect_source_files()
    stored_files: dict[str, dict[str, object]] = stored.get("files", {})

    added = sorted(set(current_files) - set(stored_files))
    removed = sorted(set(stored_files) - set(current_files))
    changed = sorted(
        rel for rel in (set(current_files) & set(stored_files))
        if current_files[rel]["sha256"] != stored_files[rel]["sha256"]
    )

    if not added and not removed and not changed:
        print(f"{GREEN}Fixture source unchanged — content/ still matches the seeded manifest.{RESET}")
        return 0

    print(f"{RED}{BOLD}Fixture source changed since the last seed:{RESET}")
    for rel in added:
        print(f"  {GREEN}+ {rel}{RESET}")
    for rel in removed:
        print(f"  {RED}- {rel}{RESET}")
    for rel in changed:
        print(f"  {YELLOW}~ {rel}{RESET}")
    print(
        f"\n{DIM}Any test failure right now may be a stale fixture, not an app bug. "
        f"Run `python scripts/testenv.py reset` to reseed.{RESET}",
    )
    return 1


# ---------------------------------------------------------------------------
# Seed
# ---------------------------------------------------------------------------

def _copy_category(folder: str) -> None:
    src_dir = paths.REAL_CONTENT_DIR / folder
    dst_dir = paths.TEST_CONTENT_DIR / folder
    dst_dir.mkdir(parents=True, exist_ok=True)

    item_indices = paths.KEPT_ITEM_ROWS.get(folder)
    kept_item_folders = _kept_item_folder_names(src_dir, folder)

    for entry in sorted(src_dir.iterdir()):
        if entry.is_dir():
            if entry.name in kept_item_folders:
                shutil.copytree(entry, dst_dir / entry.name, symlinks=False)
            continue  # dropped item folder (calculator/, bananas/, ...) — not copied
        if entry.name in paths.ITEM_SLICED_CSV_FILES:
            continue  # handled below, sliced rather than copied verbatim
        shutil.copy2(entry, dst_dir / entry.name, follow_symlinks=True)

    if item_indices is not None:
        for filename in paths.ITEM_SLICED_CSV_FILES:
            src_csv = src_dir / filename
            if not src_csv.is_file():
                continue
            rows = _read_csv_rows(src_csv)
            _write_csv_rows(dst_dir / filename, _slice_rows(rows, item_indices))


def _slice_root_csvs() -> None:
    indices = tuple(i for i, _ in paths.KEPT_CATEGORY_ROWS)
    for filename in paths.ROOT_SLICED_CSV_FILES:
        src = paths.REAL_CONTENT_DIR / filename
        if not src.is_file():
            print(f"{YELLOW}  ! root file missing, skipping: {filename}{RESET}")
            continue
        rows = _read_csv_rows(src)
        _write_csv_rows(paths.TEST_CONTENT_DIR / filename, _slice_rows(rows, indices))


def _assert_no_symlinks() -> None:
    """Walk .testenv/ and fail if anything in it is a symlink.

    Defensive regression guard on the seed script itself (see implementation plan §2,
    "Hard rule: never symlink into a project directory"), not expected to ever trigger:
    shutil.copytree/copy2 are always called here with symlinks=False / follow_symlinks=True.
    """
    offenders: list[Path] = []
    if not paths.TESTENV_DIR.exists():
        return
    for entry in paths.TESTENV_DIR.rglob("*"):
        if entry.is_symlink():
            offenders.append(entry)
    if offenders:
        print(f"{RED}{BOLD}Symlinks found under .testenv/ — refusing to proceed:{RESET}")
        for path in offenders:
            print(f"  {RED}{path}{RESET}")
        raise SystemExit(1)


def _write_test_env() -> None:
    values = {
        "TEST_CONTENT_DIR": str(paths.TEST_CONTENT_DIR),
        "TEST_ARCHIVE_DIR": str(paths.TEST_ARCHIVE_DIR),
        "TEST_CONFIG_DIR": str(paths.TEST_CONFIG_DIR),
        "TEST_DATA_DIR": str(paths.TEST_DATA_DIR),
        "TEST_OUTPUT_DIR": str(paths.TEST_OUTPUT_DIR),
        "TEST_STUB_DIR": str(paths.STUB_PROVIDER_DIR),
        "OPENAI_API_KEY": paths.TEST_OPENAI_API_KEY,
        "QWEN_API_KEY": paths.TEST_QWEN_API_KEY,
        "ELEVENLABS_API_KEY": "",
        "SPEAKTOR_API_KEY": "",
        "GEMINI_API_KEY": paths.TEST_GEMINI_API_KEY,
        "COMFYUI_API_KEY": "",
        "SECRET_KEY": paths.TEST_SECRET_KEY,
        "DEFAULT_ADMIN_PASSWORD": paths.TEST_DEFAULT_ADMIN_PASSWORD,
    }
    write_env_file(
        paths.TEST_ENV_FILE,
        values,
        header=(
            "Generated by scripts/testenv_seed.py — DO NOT edit by hand, DO NOT commit.\n"
            "Dummy credentials only. Ports are resolved at `up` time (mode-dependent), not "
            "stored here — see scripts/testenvlib/paths.py."
        ),
    )


def seed(*, force: bool) -> int:
    if paths.TEST_CONTENT_DIR.exists() and any(paths.TEST_CONTENT_DIR.iterdir()):
        if not force:
            print(
                f"{YELLOW}{paths.testenv_relative(paths.TEST_CONTENT_DIR)} already has content. "
                f"Use --force to rebuild, or `python scripts/testenv.py reset`.{RESET}",
            )
            return 1
        shutil.rmtree(paths.TEST_CONTENT_DIR)

    print(f"{BOLD}Seeding .testenv/ from {paths.testenv_relative(paths.REAL_CONTENT_DIR)}...{RESET}")

    for directory in paths.TESTENV_SUBDIRS:
        directory.mkdir(parents=True, exist_ok=True)

    _slice_root_csvs()
    for _, folder in paths.KEPT_CATEGORY_ROWS:
        _copy_category(folder)
        print(f"  copied {folder}")

    _assert_no_symlinks()

    kept_files = _collect_source_files()
    manifest = build_manifest(kept_files)
    paths.SEED_MANIFEST_PATH.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")

    _write_test_env()

    total_size = sum(int(f["size"]) for f in kept_files.values())
    print(
        f"{GREEN}Seed complete: {len(kept_files)} files, "
        f"{total_size / 1_000_000:.2f} MB, manifest at "
        f"{paths.testenv_relative(paths.SEED_MANIFEST_PATH)}{RESET}",
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--force", action="store_true", help="wipe an existing .testenv/content and rebuild")
    parser.add_argument(
        "--check-ts", action="store_true",
        help="compare content/ against the stored manifest without reseeding; exit 1 on drift",
    )
    args = parser.parse_args()

    if not paths.REAL_CONTENT_DIR.is_dir():
        print(f"{RED}Real content directory not found: {paths.REAL_CONTENT_DIR}{RESET}", file=sys.stderr)
        return 1

    if args.check_ts:
        return check_timestamps()
    return seed(force=args.force)


if __name__ == "__main__":
    raise SystemExit(main())
