"""The one shape every finding source (e2e/pytest/pylint/detector/manual) maps to."""

from __future__ import annotations

from dataclasses import dataclass, field

SEVERITIES = ("high", "medium", "low")
SOURCES = ("e2e", "pytest", "pylint", "detector", "manual")


@dataclass(frozen=True)
class Finding:
    """A single problem to track as an issue.

    `fingerprint_key` is the source-specific stable string fed to
    fingerprint.compute() — NOT the fingerprint itself (each sources/*.py module builds
    this from whatever uniquely and stably identifies the underlying problem for that
    source; see implementation_plan.md §11 for why each one was chosen).
    """

    source: str  # one of SOURCES
    title: str
    body: str  # markdown: evidence, affected files, acceptance criteria
    fingerprint_key: str
    severity: str = "medium"  # one of SEVERITIES
    affected_files: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.source not in SOURCES:
            raise ValueError(f"Unknown finding source: {self.source!r} (expected one of {SOURCES})")
        if self.severity not in SEVERITIES:
            raise ValueError(f"Unknown severity: {self.severity!r} (expected one of {SEVERITIES})")
        if not self.fingerprint_key.strip():
            raise ValueError(f"Finding {self.title!r} has an empty fingerprint_key")
