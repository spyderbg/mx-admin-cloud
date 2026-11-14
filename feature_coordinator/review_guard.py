"""Security S1 — the reviewer's confidentiality guard: KNOWN protected content never reaches the model, whichever
field of the prompt would carry it.

What is known. The content of every tracked SECRET file (`runtime.secret_patterns` without an exempt suffix; each
path component judged on its own) at the diff base, at the reviewed head and in every commit between them (a
secret added and removed again inside the range is known too). From each such file the guard keeps, in memory
only, its TOKENS: every line, and the value of every `KEY=VALUE` / `KEY: VALUE` line, of at least MIN_SECRET
characters after normalisation. It also keeps the blob IDs of all protected or secret files at base and head
(byte-identical copies).

How it matches. Text is normalised first — Unicode NFKC, then every whitespace, control and format character
removed — so line endings (LF, CRLF, U+2028), indentation, zero-width characters, compatibility forms and a token
wrapped across lines all compare equal. A token matches as a SUBSTRING of the normalised text: inside a commit
subject, a sentence, a file name, a diff line or an assignment.

Where it is applied (review_context.build_context):
  1. per changed file — a file whose diff (or path) repeats a token is withheld as a whole (listed, never shown);
  2. per untrusted field — a commit subject, the Issue title or body, the plan, a conventions excerpt that repeats a
     token is replaced as a whole by a marker (withholding, never partial sanitisation), with a coordinator note;
  3. at the FINAL BOUNDARY — the complete prompt, after every other sanitiser, is checked once more; if a token is
     still in it, no prompt exists (`problem`: an ERROR review, no model call). Step 3 is the guarantee; 1 and 2
     keep reviews useful and are defence in depth.

The guard never prints, logs or raises a token: findings name the SOURCE PATH of the secret only.

Limits (documented, not silently accepted; feature-workflow.md §25.7): Git history older than the reviewed range is
not consulted; a secret file's PATH appears in the coordinator's messages even when the path itself contains
secret text; content that was never tracked in the reviewed range (an ignored
`.env` copied into code) is unknown to the guard — on the bwrap backend agents cannot read such files (they are
masked), on native-cli an agent that copied one has already sent it to a model; lines and values shorter than
MIN_SECRET characters; and content transformed beyond normalisation (encoded, split by other characters,
re-cased). Credential-shaped strings are additionally redacted by review_context.clean(). More than
MAX_SECRET_BYTES / MAX_SECRET_TOKENS of known secret content cannot be guarded: the review is then refused.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Iterator

MIN_SECRET = 12             # normalised characters; shorter lines and values are not matched
MAX_SECRET_BYTES = 1 << 20  # known secret content read to build the guard (more: not reviewed)
MAX_SECRET_TOKENS = 20_000
EMPTY_BLOB = "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"
WITHHELD = "[withheld by the coordinator: this text repeats the content of a protected file]"
_ASSIGNMENT = re.compile(r"^\s*(?:export\s+)?[^\s=:]+\s*[=:]\s*(.+)$")
# CR, LF, VT, FF, NEL and the Unicode line and paragraph separators (written as code points: no invisible text here)
_LINE_BREAKS = re.compile("[\r\n\x0b\x0c\x85" + chr(0x2028) + chr(0x2029) + "]+")


def compact(text: str) -> str:
    """*text* as it is compared: NFKC, without whitespace, control and format characters."""
    return "".join(ch for ch in unicodedata.normalize("NFKC", text)
                   if not ch.isspace() and unicodedata.category(ch)[0] != "C")


def tokens_of(text: str) -> Iterator[str]:
    """The normalised lines, and assignment values, of *text* that are long enough to be matched."""
    for line in _LINE_BREAKS.split(text):
        whole = compact(line)
        if len(whole) >= MIN_SECRET:
            yield whole
        assigned = _ASSIGNMENT.match(line)
        if assigned:
            value = compact(assigned.group(1)).strip("'\"`,;")
            if len(value) >= MIN_SECRET:
                yield value


@dataclass
class SecretGuard:
    """Known protected content (see the module docstring). Tokens live in memory only."""

    tokens: dict[str, str] = field(default_factory=dict)   # normalised token → the secret path it comes from
    blobs: dict[str, str] = field(default_factory=dict)    # blob ID → a protected or secret path holding it
    trees: dict[str, dict[str, str]] = field(default_factory=dict)  # commit → path → blob ID (read once)
    budget: int = MAX_SECRET_BYTES
    problem: str = ""

    def add(self, source: str, data: bytes) -> None:
        """Learn the content of the secret file *source*."""
        self.budget -= len(data)
        if self.budget < 0:
            self.problem = (f"more than {MAX_SECRET_BYTES} bytes of tracked secret content: the confidentiality "
                            "guard cannot be built, so nothing is reviewed")
            return
        for token in tokens_of(data.decode("utf-8", errors="replace")):
            self.tokens.setdefault(token, source)
        if len(self.tokens) > MAX_SECRET_TOKENS:
            self.problem = (f"more than {MAX_SECRET_TOKENS} lines and values of tracked secret content: the "
                            "confidentiality guard cannot be built, so nothing is reviewed")

    def copied_from(self, blob: str | None) -> str | None:
        """The protected or secret path whose content is byte-identical to *blob* (None: none)."""
        return self.blobs.get(blob) if blob and blob != EMPTY_BLOB else None

    def find(self, text: str) -> str | None:
        """The secret PATH whose content *text* repeats (None: it repeats none). Never returns the content."""
        if not self.tokens or not text:
            return None
        haystack = compact(text)
        if len(haystack) < MIN_SECRET:
            return None
        return next((source for token, source in self.tokens.items() if token in haystack), None)

    def repeats(self, text: str) -> str | None:
        """find() — the name the per-file diff guard used before the guard covered every field."""
        return self.find(text)
