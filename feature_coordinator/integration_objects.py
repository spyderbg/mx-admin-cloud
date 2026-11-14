"""W4.7 (M1, moved here in M2): hash-verified Git objects of an integration source — the strict commit parser and the
`git cat-file --batch` reader that recomputes every object's SHA-1 (Codex review of M1, findings 1–2).

Moved out of integration_intake.py unchanged (M2 only added ObjectMissing and the generic read_object, which trees and
blobs use exactly like commits, integration_paths.py); integration_intake re-exports every name, so existing imports
and the M1 tests are unaffected. The reader is started by ReadOnlyGit in its private repository
(integration_isolation.py), never with the checkout as the repository.
"""

from __future__ import annotations

import hashlib
import os
import re
import signal
import subprocess
import tempfile
import threading
import unicodedata
from dataclasses import dataclass
from typing import IO, Any, Callable, NoReturn

from .errors import GitOperationError
from .integration import clean_text

MAX_COMMIT_OBJECT = 16 << 20   # a larger commit object is refused (range) or not verifiable (base)


class HistoryUnverifiable(GitOperationError):
    """The checkout's view of the source history is incomplete or does not match the commit objects (a missing
    object, a shallow or grafted history, a corrupt object, unexpected Git output). The intake DEFERS — the job
    stays in INTAKE — and never accepts."""


class ObjectMissing(HistoryUnverifiable):
    """M2: an object the read needs is not in the checkout (nothing is ever fetched)."""


class HistoryRefused(GitOperationError):
    """A property of the (hash-verified) source history fails the rules — a malformed commit object, a range or
    fork point beyond the limits. The intake REFUSES."""


@dataclass(frozen=True)
class Identity:
    """An author or committer of a commit object, as recorded (no mailmap)."""

    name: str
    email: str
    time: int

    @property
    def key(self) -> str:
        """The e-mail compared with github.integration.known_authors: ASCII letters lowercased, nothing else
        (no Unicode case folding: `K` KELVIN SIGN must not become `k`)."""
        return self.email.encode("utf-8").lower().decode("utf-8")

    def shown(self) -> str:
        """`Name <email>` for a record (one printable line)."""
        return clean_text(f"{self.name} <{self.email}>", 200)


@dataclass(frozen=True)
class CommitObject:
    """A hash-verified commit object: its SHA, tree, parents (in order), author and committer."""

    sha: str
    tree: str
    parents: tuple[str, ...]
    author: Identity
    committer: Identity


_HEX40 = rb"[0-9a-f]{40}"
_IDENT_RE = re.compile(rb"(?P<name>[^<>\n\x00]*) <(?P<email>[^<>\n\x00]*)> (?P<time>[0-9]{1,20}) (?P<tz>[+-][0-9]{4})")
_EXTRA_HEADER_RE = re.compile(rb"[a-z][a-z0-9-]{0,63} .*", re.DOTALL)
_CORE_HEADERS = (b"tree ", b"parent ", b"author ", b"committer ")


def parse_commit(sha: str, body: bytes, *, strict: bool = True) -> CommitObject:
    """The commit object *body* of *sha* (already hash-verified), parsed by its exact standard form:

        tree <sha>\n (parent <sha>\n)* author <ident>\n committer <ident>\n (<key> <value>\n ( <continuation>\n)*)*
        \n <message>

    with <ident> = `<name> <<email>> <unix time> <+-hhmm>`. Anything else — a missing or repeated core header,
    headers out of order, a NUL byte, a control character in a name, an e-mail that is empty, not UTF-8 or contains
    whitespace or control characters — is malformed: HistoryRefused when *strict* (a commit to integrate),
    HistoryUnverifiable otherwise (a base commit read only for its parents; identities are not checked there)."""
    def bad(reason: str) -> NoReturn:
        error = HistoryRefused if strict else HistoryUnverifiable
        raise error(f"commit {sha[:12]} is malformed: {reason}")

    header, separator, _message = body.partition(b"\n\n")
    if not separator:
        bad("no blank line ends its header")
    if b"\x00" in header:
        bad("its header contains a NUL byte")
    lines = header.split(b"\n")
    tree = re.fullmatch(b"tree (" + _HEX40 + b")", lines[0])
    if tree is None:
        bad("its first header line is not `tree <sha>`")
    index, parents = 1, []
    while index < len(lines) and lines[index].startswith(b"parent "):
        parent = re.fullmatch(b"parent (" + _HEX40 + b")", lines[index])
        if parent is None:
            bad(f"invalid parent line {_shown_bytes(lines[index])}")
        parents.append(parent.group(1).decode("ascii"))
        index += 1
    if strict and len(set(parents)) != len(parents):
        bad("it names a parent twice")
    identities = []
    for role in (b"author", b"committer"):
        if index >= len(lines) or not lines[index].startswith(role + b" "):
            bad(f"no {role.decode()} line where Git writes it")
        identities.append(_identity(lines[index][len(role) + 1:], role.decode(), strict, bad))
        index += 1
    extra = False
    for line in lines[index:]:
        if line.startswith(b" ") and extra:
            continue  # continuation of a multi-line extra header (gpgsig, mergetag)
        if line.startswith(_CORE_HEADERS) or not _EXTRA_HEADER_RE.fullmatch(line):
            bad(f"unexpected header line {_shown_bytes(line)}")
        extra = True
    return CommitObject(sha=sha, tree=tree.group(1).decode("ascii"), parents=tuple(parents),
                        author=identities[0], committer=identities[1])


# Bidirectional-text controls: a name containing one could display as another (Trojan Source).
_BIDI_CONTROLS = frozenset("\u061c\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069")


def _identity(raw: bytes, role: str, strict: bool, bad: Callable[[str], NoReturn]) -> Identity:
    match = _IDENT_RE.fullmatch(raw)
    if match is None:
        if strict:
            bad(f"its {role} is not `Name <email> time zone`: {_shown_bytes(raw)}")
        return Identity(name="", email="", time=0)
    name = match.group("name").decode("utf-8", "replace")
    try:
        email = match.group("email").decode("utf-8")
    except UnicodeDecodeError:
        email = ""
        if strict:
            bad(f"its {role} e-mail is not UTF-8")
    if strict:
        if not email or any(ch.isspace() or unicodedata.category(ch).startswith("C") for ch in email):
            bad(f"its {role} e-mail {email!r} is empty or contains whitespace or control characters")
        if any(unicodedata.category(ch) == "Cc" or ch in _BIDI_CONTROLS for ch in name):
            bad(f"its {role} name {name!r} contains control or bidirectional-text characters")
    return Identity(name=name, email=email, time=int(match.group("time")))


def _shown_bytes(raw: bytes) -> str:
    return clean_text(repr(raw[:120]), 160)


def commit_object_sha(body: bytes) -> str:
    """The SHA-1 object ID Git gives a commit object with this content."""
    return object_sha("commit", body)


def object_sha(kind: str, body: bytes) -> str:
    """The SHA-1 object ID Git gives an object of type *kind* with this content."""
    return hashlib.sha1(kind.encode("ascii") + b" %d\x00" % len(body) + body).hexdigest()  # nosec - Git's object ID


class _CommitReader:
    """One `git cat-file --batch` process: one object per request, length-delimited output, every commit object's
    SHA-1 recomputed. A missing object, another object type, a corrupt object or unexpected output raises
    HistoryUnverifiable; the whole read is bounded by *timeout* (the process group is killed)."""

    def __init__(self, argv: list[str], env: dict[str, str], timeout: float) -> None:
        self._stderr: IO[bytes] = tempfile.TemporaryFile()  # pylint: disable=consider-using-with
        self._proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,  # pylint: disable=consider-using-with
                                      stderr=self._stderr, env=env, start_new_session=True)
        self.timed_out = False
        self._timer = threading.Timer(timeout, self._kill)
        self._timer.daemon = True
        self._timer.start()
        self.count = 0

    def _kill(self) -> None:
        self.timed_out = True
        try:
            os.killpg(self._proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            self._proc.kill()

    def read(self, sha: str, *, strict: bool) -> CommitObject:
        """The verified, parsed commit *sha*."""
        return parse_commit(sha, self.read_object(sha, "commit", limit=MAX_COMMIT_OBJECT, strict=strict),
                            strict=strict)

    def read_object(self, sha: str, kind: str, *, limit: int, strict: bool) -> bytes:
        """The raw body of the object *sha*, which must be of type *kind* (commit, tree, blob) and hash to *sha*
        (M2: trees and blobs are verified exactly like commits). Larger than *limit*: HistoryRefused when *strict*
        (an object of the source), HistoryUnverifiable otherwise."""
        stdin, stdout = self._proc.stdin, self._proc.stdout
        assert stdin is not None and stdout is not None
        try:
            stdin.write(sha.encode("ascii") + b"\n")
            stdin.flush()
            header = stdout.readline(200)
        except OSError as exc:
            raise self._failed(f"reading {sha[:12]}: {exc}") from exc
        if header == f"{sha} missing\n".encode("ascii"):
            raise ObjectMissing(f"{kind} {sha[:12]} is not present in this checkout: the history is "
                                      "incomplete (shallow, partial or damaged clone); fetch the full history")
        match = re.fullmatch(b"(" + _HEX40 + b") ([a-z]{1,10}) ([0-9]{1,12})\n", header)
        if match is None or match.group(1).decode("ascii") != sha:
            raise self._failed(f"unexpected output for {sha[:12]}: {_shown_bytes(header)}")
        size = int(match.group(3))
        if size > limit:
            self.close()
            error = HistoryRefused if strict else HistoryUnverifiable
            raise error(f"{kind} {sha[:12]} is larger than {limit} bytes")
        body = stdout.read(size)
        if len(body) != size or stdout.read(1) != b"\n":
            raise self._failed(f"truncated output for {sha[:12]}")
        if match.group(2) != kind.encode("ascii"):
            raise HistoryUnverifiable(f"object {sha[:12]} is a {match.group(2).decode('ascii')}, not a {kind}")
        if object_sha(kind, body) != sha:
            raise HistoryUnverifiable(f"the object stored as {sha[:12]} does not hash to that SHA: the checkout's "
                                      "object store is corrupt or was tampered with (`git fsck`)")
        self.count += 1
        return body

    def _failed(self, detail: str) -> HistoryUnverifiable:
        self._stop()
        if self.timed_out:
            error = HistoryUnverifiable(f"reading the commit objects did not finish in time ({detail})")
        else:
            self._stderr.seek(0)
            stderr = clean_text(self._stderr.read(400).decode("utf-8", "replace"), 200)
            error = HistoryUnverifiable(f"git cat-file failed: {detail}{f' ({stderr})' if stderr else ''}")
        self.close()
        return error

    def _stop(self) -> None:
        """End the process and close its pipes (idempotent)."""
        self._timer.cancel()
        if self._proc.poll() is None:
            try:
                if self._proc.stdin and not self._proc.stdin.closed:
                    self._proc.stdin.close()
                self._proc.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                self._kill()
                self._proc.wait()
        for stream in (self._proc.stdout, self._proc.stdin):
            if stream is not None and not stream.closed:
                stream.close()

    def close(self) -> None:
        """Stop the process and release everything (idempotent)."""
        self._stop()
        if not self._stderr.closed:
            self._stderr.close()

    def __enter__(self) -> "_CommitReader":
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()
