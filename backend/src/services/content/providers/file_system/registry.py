"""Row-aligned CSV registries: an id file plus per-language files that must change together.

Two registries are kept this way:

- the **category registry** in the content root: ``categories_ids.csv``, the names in
  ``categories_<lang>.csv`` and the descriptions in ``descriptions_<lang>.csv``;
- each category's **item registry**: ``items_ids.csv`` and the names in ``items_<lang>.csv``.

Row N of every file belongs to the id on row N of the id file, so:

- **Locked.** Every read and write holds the registry's exclusive lock: a file lock
  (``.<name>_registry.lock`` next to the files) taken under a process-wide re-entrant lock, which
  serializes the threadpool workers of one process and the separate HTTP and Unix-socket backend
  processes that share the content directory. Lock order: the category registry before an item
  registry, never the other way round.
- **Aligned.** Blank rows are kept, never dropped, so an empty value cannot shift the rows below it
  onto the wrong id; files of different lengths are padded rather than truncated.
- **Complete.** A column covers the configured languages plus every other language file found on
  disk (e.g. ``descriptions_it.csv``), so adding or removing a row keeps those files aligned too.
- **All or nothing.** A write stages every file as a hidden temp file, then creates the commit
  marker ``.<name>_registry.commit`` and only then renames the temp files over the real ones.
  Taking the lock finishes an interrupted write: with the marker present the remaining renames
  are applied, without it the staged files are discarded. A crash or a killed worker (e.g. a
  ``--reload`` restart) can therefore never leave the files half-updated.
"""

from __future__ import annotations

import contextlib
import csv
import os
import re
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import IO

from src.core.config import settings
from src.services.content.models import ContentOperationError
from src.services.content.providers.file_system.constants import (
    _CATEGORIES_IDS,
    _CATEGORIES_LANG_CSV,
    _DESCRIPTIONS_LANG_CSV,
    _ITEMS_IDS,
    _ITEMS_LANG_CSV,
)

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX hosts only get the in-process lock
    fcntl = None  # type: ignore[assignment]

_LANGUAGE_RE = re.compile(r"^[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})?$")


@dataclass(frozen=True)
class RegistrySpec:
    """The files of one registry: the id file and, per column, a per-language file name pattern."""

    name: str  # names the lock file and the commit marker
    ids_file: str
    columns: tuple[tuple[str, str], ...]  # (column, file name pattern with "{}" for the language)

    def staged_globs(self) -> set[str]:
        """Glob patterns matching the staged temp files of this registry's files."""
        return {f".{self.ids_file}.tmp", *(f".{pattern.format('*')}.tmp" for _, pattern in self.columns)}


ITEMS = RegistrySpec("items", _ITEMS_IDS, (("names", _ITEMS_LANG_CSV),))
CATEGORIES = RegistrySpec(
    "categories", _CATEGORIES_IDS, (("names", _CATEGORIES_LANG_CSV), ("descriptions", _DESCRIPTIONS_LANG_CSV)),
)

# Guards _held_locks and serializes registry access between the threads of this process; the
# file lock does the same between processes. Re-entrant, so a locked section may read again.
_process_lock = threading.RLock()
_held_locks: dict[str, list] = {}  # lock file path -> [open lock file or None, depth]


class RegistryCommitPending(ContentOperationError):
    """The write was committed but not fully applied; the next registry access completes it.

    Callers must not undo their own side effects (folders, files) for this error, since the
    registry change they belong to is already durable.
    """


@dataclass
class Registry:
    """The ids and, per column and language, the values; every list has the same length."""

    ids: list[str]
    columns: dict[str, dict[str, list[str]]]

    @property
    def names(self) -> dict[str, list[str]]:
        """Names by language."""
        return self.columns["names"]

    @property
    def descriptions(self) -> dict[str, list[str]]:
        """Descriptions by language (category registry only)."""
        return self.columns["descriptions"]

    def index_of(self, row_id: str) -> int | None:
        """Row index of *row_id*, or None when it is not registered."""
        try:
            return self.ids.index(row_id)
        except ValueError:
            return None

    def append(self, row_id: str, **values: dict[str, str]) -> int:
        """Add a row for *row_id* and return its index; missing values are blank.

        *values* maps a column to its values by language, e.g. ``names={"en": "apple"}``.
        """
        unknown = set(values) - set(self.columns)
        if unknown:
            raise KeyError(f"Unknown registry column(s): {', '.join(sorted(unknown))}")
        self.ids.append(row_id.strip())
        for column, by_lang in self.columns.items():
            for lang, column_values in by_lang.items():
                column_values.append((values.get(column, {}).get(lang) or "").strip())
        return len(self.ids) - 1

    def remove(self, index: int) -> None:
        """Delete the row at *index* from every file."""
        del self.ids[index]
        for by_lang in self.columns.values():
            for column_values in by_lang.values():
                del column_values[index]

    def row(self, index: int, languages: list[str] | None = None) -> dict[str, dict[str, str]]:
        """The non-blank values of row *index* per column and language, optionally limited to *languages*."""
        return {
            column: {
                lang: values[index] for lang, values in by_lang.items()
                if values[index] and (languages is None or lang in languages)
            }
            for column, by_lang in self.columns.items()
        }


def _staged_path(path: Path) -> Path:
    return path.with_name(f".{path.name}.tmp")


def _final_path(staged: Path) -> Path:
    return staged.with_name(staged.name[1:-len(".tmp")])


def _commit_marker(folder: Path, spec: RegistrySpec) -> Path:
    return folder / f".{spec.name}_registry.commit"


def _recover(folder: Path, spec: RegistrySpec) -> None:
    """Finish a committed but interrupted write, or discard an uncommitted one."""
    marker = _commit_marker(folder, spec)
    staged = sorted({path for pattern in spec.staged_globs() for path in folder.glob(pattern)})
    if marker.exists():
        for path in staged:
            os.replace(path, _final_path(path))
        marker.unlink()
    else:
        for path in staged:
            path.unlink(missing_ok=True)


@contextlib.contextmanager
def registry_lock(folder: Path, spec: RegistrySpec) -> Iterator[None]:
    """Hold the registry's lock; the outermost entry also recovers an interrupted write.

    When the lock file cannot be created (a read-only or missing folder), only the in-process
    lock is held: such a folder cannot be written by anyone, so reads stay safe.
    """
    key = os.path.abspath(folder / f".{spec.name}_registry.lock")
    with _process_lock:
        entry = _held_locks.get(key)
        if entry is None:
            entry = [_open_file_lock(key), 0]
            _held_locks[key] = entry
            try:
                _recover(folder, spec)
            except OSError as exc:
                _release(key, entry)
                raise ContentOperationError(
                    f"Could not complete an interrupted {spec.name} registry write: {exc}",
                ) from exc
        entry[1] += 1
        try:
            yield
        finally:
            entry[1] -= 1
            if entry[1] == 0:
                _release(key, entry)


def _open_file_lock(path: str) -> IO[bytes] | None:
    try:
        fh = open(path, "ab")  # pylint: disable=consider-using-with  # closed by _release
    except OSError:
        return None
    if fcntl is not None:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        except OSError:
            fh.close()
            raise
    return fh


def _release(key: str, entry: list) -> None:
    del _held_locks[key]
    if entry[0] is not None:
        entry[0].close()  # closing the file releases the flock


def _read_rows(path: Path) -> list[str]:
    """One value per row of *path*, blank rows included; [] when the file does not exist.

    The files have a single column, so a row with several fields is a value with unquoted commas
    (typically a hand edit): its fields are joined back instead of all but the first being lost.
    """
    try:
        with open(path, newline="", encoding="utf-8") as fh:
            return [",".join(row).strip() for row in csv.reader(fh)]
    except FileNotFoundError:
        return []
    except (OSError, csv.Error, UnicodeDecodeError) as exc:
        # Never treat an unreadable file as empty: the next write would replace it with almost nothing.
        raise ContentOperationError(f"Could not read '{path.name}': {exc}") from exc


def _languages(folder: Path, spec: RegistrySpec, pattern: str) -> list[str]:
    """The configured languages, then any other language that has a file for *pattern* on disk."""
    prefix, suffix = pattern.split("{}")
    found = set()
    with contextlib.suppress(OSError):
        for path in folder.glob(pattern.format("*")):
            lang = path.name[len(prefix):len(path.name) - len(suffix)]
            if path.name != spec.ids_file and _LANGUAGE_RE.match(lang):
                found.add(lang)
    configured = list(settings.SUPPORTED_LANGUAGES)
    return configured + sorted(found - set(configured))


def read_registry(folder: Path, spec: RegistrySpec) -> Registry:
    """Read the registry in *folder*. Call it while holding registry_lock()."""
    ids = _read_rows(folder / spec.ids_file)
    columns = {
        column: {lang: _read_rows(folder / pattern.format(lang)) for lang in _languages(folder, spec, pattern)}
        for column, pattern in spec.columns
    }
    all_lists = [ids, *(values for by_lang in columns.values() for values in by_lang.values())]
    length = max(len(values) for values in all_lists)
    # Rows blank in every file (e.g. trailing empty lines) belong to no id.
    while length and not any(values[length - 1] for values in all_lists if len(values) >= length):
        length -= 1
    for values in all_lists:
        del values[length:]
        values.extend([""] * (length - len(values)))
    return Registry(ids=ids, columns=columns)


def _write_staged(path: Path, rows: list[str]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        for row in rows:
            writer.writerow([row])
        fh.flush()
        os.fsync(fh.fileno())


def write_registry(folder: Path, spec: RegistrySpec, registry: Registry) -> None:
    """Replace every file of the registry at once. Call it while holding registry_lock().

    Raises ContentOperationError with nothing changed when staging fails, and
    RegistryCommitPending when the commit is durable but could not be applied yet.
    """
    files = {folder / spec.ids_file: registry.ids}
    for column, pattern in spec.columns:
        files.update({folder / pattern.format(lang): values for lang, values in registry.columns[column].items()})
    marker = _commit_marker(folder, spec)
    try:
        for path, rows in files.items():
            _write_staged(_staged_path(path), rows)
        with open(marker, "wb") as fh:
            os.fsync(fh.fileno())
    except OSError as exc:
        marker.unlink(missing_ok=True)
        for path in files:
            _staged_path(path).unlink(missing_ok=True)
        raise ContentOperationError(f"Failed to write the {spec.name} registry: {exc}") from exc

    # Committed: from here on the change is applied, now or by the next _recover().
    try:
        _recover(folder, spec)
    except OSError as exc:
        raise RegistryCommitPending(
            f"The {spec.name} registry change was saved but not fully applied; "
            f"it completes on the next access: {exc}",
        ) from exc
