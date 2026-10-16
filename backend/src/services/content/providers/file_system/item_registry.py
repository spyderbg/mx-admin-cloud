"""The item registry of one category: the row-aligned items_ids.csv / items_<lang>.csv files.

An item is one row index across all of these files, so they only ever change together:

- **Locked.** Every read and write holds an exclusive lock for the category. It is a file lock
  (``.items_registry.lock`` in the category folder) taken under a process-wide re-entrant lock, so
  it serializes the threadpool workers of one process and the separate HTTP and Unix-socket
  backend processes that share the content directory.
- **Aligned.** Blank rows are kept, never dropped, so an empty name cannot shift the rows below it
  onto the wrong item; files of different lengths are padded rather than truncated.
- **All or nothing.** A write stages every file as a hidden temp file, then creates the commit
  marker ``.items_registry.commit`` and only then renames the temp files over the real ones.
  Taking the lock finishes an interrupted write: with the marker present the remaining renames
  are applied, without it the staged files are discarded. A crash or a killed worker (e.g. a
  ``--reload`` restart) can therefore never leave the files half-updated.
"""

from __future__ import annotations

import contextlib
import csv
import os
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import IO

from src.core.config import settings
from src.services.content.models import ContentOperationError
from src.services.content.providers.file_system.constants import _ITEMS_IDS, _ITEMS_LANG_CSV

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX hosts only get the in-process lock
    fcntl = None  # type: ignore[assignment]

_LOCK_FILE = ".items_registry.lock"
_COMMIT_MARKER = ".items_registry.commit"
_STAGED_GLOB = ".items_*.csv.tmp"

# Guards _held_locks and serializes registry access between the threads of this process; the
# file lock does the same between processes. Re-entrant, so a locked section may read again.
_process_lock = threading.RLock()
_held_locks: dict[str, list] = {}  # lock file path -> [open lock file or None, depth]


class ItemRegistryCommitPending(ContentOperationError):
    """The write was committed but not fully applied; the next registry access completes it.

    Callers must not undo their own side effects (folders, files) for this error, since the
    registry change they belong to is already durable.
    """


@dataclass
class ItemRegistry:
    """Item ids and their per-language names; every list has the same length."""

    ids: list[str]
    names: dict[str, list[str]]

    def index_of(self, item_id: str) -> int | None:
        """Row index of *item_id*, or None when it is not registered."""
        try:
            return self.ids.index(item_id)
        except ValueError:
            return None

    def append(self, item_id: str, names_by_lang: dict[str, str]) -> None:
        """Add a row for *item_id*; a language missing from *names_by_lang* gets a blank name."""
        self.ids.append(item_id.strip())
        for lang, values in self.names.items():
            values.append((names_by_lang.get(lang) or "").strip())

    def remove(self, index: int) -> None:
        """Delete the row at *index* from every file."""
        del self.ids[index]
        for values in self.names.values():
            del values[index]


def _staged_path(path: Path) -> Path:
    return path.with_name(f".{path.name}.tmp")


def _final_path(staged: Path) -> Path:
    return staged.with_name(staged.name[1:-len(".tmp")])


def _recover(category_path: Path) -> None:
    """Finish a committed but interrupted write, or discard an uncommitted one."""
    marker = category_path / _COMMIT_MARKER
    staged = sorted(category_path.glob(_STAGED_GLOB))
    if marker.exists():
        for path in staged:
            os.replace(path, _final_path(path))
        marker.unlink()
    else:
        for path in staged:
            path.unlink(missing_ok=True)


@contextlib.contextmanager
def item_registry_lock(category_path: Path) -> Iterator[None]:
    """Hold the category's registry lock; the outermost entry also recovers an interrupted write.

    When the lock file cannot be created (a read-only content directory), only the in-process
    lock is held: such a directory cannot be written by anyone, so reads stay safe.
    """
    key = os.path.abspath(category_path / _LOCK_FILE)
    with _process_lock:
        entry = _held_locks.get(key)
        if entry is None:
            entry = [_open_file_lock(key), 0]
            _held_locks[key] = entry
            try:
                _recover(category_path)
            except OSError as exc:
                _release(key, entry)
                raise ContentOperationError(f"Could not complete an interrupted item registry write: {exc}") from exc
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
    """First-column values of *path*, blank rows included; [] when the file does not exist."""
    try:
        with open(path, newline="", encoding="utf-8") as fh:
            return [(row[0].strip() if row else "") for row in csv.reader(fh)]
    except FileNotFoundError:
        return []
    except (OSError, csv.Error, UnicodeDecodeError) as exc:
        # Never treat an unreadable file as empty: the next write would replace it with almost nothing.
        raise ContentOperationError(f"Could not read '{path.name}': {exc}") from exc


def read_item_registry(category_path: Path) -> ItemRegistry:
    """Read the category's registry. Call it while holding item_registry_lock()."""
    ids = _read_rows(category_path / _ITEMS_IDS)
    names = {lang: _read_rows(category_path / _ITEMS_LANG_CSV.format(lang)) for lang in settings.SUPPORTED_LANGUAGES}
    columns = [ids, *names.values()]
    length = max(len(column) for column in columns)
    # Rows blank in every file (e.g. trailing empty lines) belong to no item.
    while length and not any(column[length - 1] for column in columns if len(column) >= length):
        length -= 1
    for column in columns:
        del column[length:]
        column.extend([""] * (length - len(column)))
    return ItemRegistry(ids=ids, names=names)


def _write_staged(path: Path, rows: list[str]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        for row in rows:
            writer.writerow([row])
        fh.flush()
        os.fsync(fh.fileno())


def write_item_registry(category_path: Path, registry: ItemRegistry) -> None:
    """Replace every registry file of the category at once. Call it while holding item_registry_lock().

    Raises ContentOperationError with nothing changed when staging fails, and
    ItemRegistryCommitPending when the commit is durable but could not be applied yet.
    """
    files = {category_path / _ITEMS_IDS: registry.ids}
    files.update({category_path / _ITEMS_LANG_CSV.format(lang): values for lang, values in registry.names.items()})
    marker = category_path / _COMMIT_MARKER
    try:
        for path, rows in files.items():
            _write_staged(_staged_path(path), rows)
        with open(marker, "wb") as fh:
            os.fsync(fh.fileno())
    except OSError as exc:
        marker.unlink(missing_ok=True)
        for path in files:
            _staged_path(path).unlink(missing_ok=True)
        raise ContentOperationError(f"Failed to write the item registry: {exc}") from exc

    # Committed: from here on the change is applied, now or by the next _recover().
    try:
        _recover(category_path)
    except OSError as exc:
        raise ItemRegistryCommitPending(
            f"The item registry change was saved but not fully applied; it completes on the next access: {exc}",
        ) from exc
