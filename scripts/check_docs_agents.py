#!/usr/bin/env python3
"""Validate the agent documentation tree under docs_agents/.

Usage:
    python scripts/check_docs_agents.py               # every check
    python scripts/check_docs_agents.py --skip-drift  # skip the export comparison (no backend import)

Checks (each prints PASS/FAIL; the exit code is 1 when any check fails):
    links      every relative Markdown link under docs_agents/ resolves, including #anchors
               into Markdown files (GitHub heading slugs)
    services   every backend service package has docs_agents/backend/services/<name>/README.md
               and openapi.yaml
    providers  each service README names every provider class registered in its providers/
               package and every configuration file in its backend/config/<config_subdir>/
    openapi    every openapi.yaml under docs_agents/ is structurally valid OpenAPI 3.x: version,
               info, paths, non-empty responses, declared path parameters, known security
               schemes, unique operationIds and resolvable local $refs. Full JSON Schema
               validation needs a validator package this repository does not install.
    readmes    docs_agents/README.md links every pre-existing tracked README.md (vendored
               assets excluded), so legacy documentation stays discoverable
    drift      `export_openapi.py --docs-agents --check` reports no drift (imports the backend)

Read-only: this script never writes a file.
"""

from __future__ import annotations

import argparse
import ast
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Iterator

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCS_ROOT = REPO_ROOT / "docs_agents"
SERVICES_DIR = REPO_ROOT / "backend" / "src" / "services"
CONFIG_DIR = REPO_ROOT / "backend" / "config"

HTTP_METHODS = ("get", "put", "post", "delete", "options", "head", "patch", "trace")
README_INDEX_EXCLUDED_PREFIXES = ("frontend/src/assets/vendors/", "docs_agents/")

GREEN = "\033[92m"
RED = "\033[91m"
BOLD = "\033[1m"
RESET = "\033[0m"

_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")


# ---------------------------------------------------------------------------
# Markdown helpers
# ---------------------------------------------------------------------------

def _prose_lines(text: str) -> Iterator[str]:
    """Lines outside fenced code blocks."""
    fenced = False
    for line in text.splitlines():
        if _FENCE_RE.match(line):
            fenced = not fenced
            continue
        if not fenced:
            yield line


def github_slug(heading: str) -> str:
    """The anchor GitHub generates for a Markdown heading (before de-duplication)."""
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", heading.strip().lower())
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


def heading_anchors(text: str) -> set[str]:
    """Every anchor a Markdown document exposes, with GitHub's -1, -2 suffixes for repeats."""
    seen: Counter[str] = Counter()
    anchors: set[str] = set()
    for line in _prose_lines(text):
        match = _HEADING_RE.match(line)
        if not match:
            continue
        slug = github_slug(match.group(2))
        anchors.add(slug if not seen[slug] else f"{slug}-{seen[slug]}")
        seen[slug] += 1
    return anchors


def markdown_links(text: str) -> list[str]:
    """Link targets in *text*, ignoring fenced code blocks."""
    return [target for line in _prose_lines(text) for target in _LINK_RE.findall(line)]


# ---------------------------------------------------------------------------
# Checks — each returns a list of problems (empty = pass)
# ---------------------------------------------------------------------------

def check_links(docs_root: Path) -> list[str]:
    """Relative links (and #anchors into Markdown files) under *docs_root* that do not resolve."""
    problems = []
    for doc in sorted(docs_root.rglob("*.md")):
        text = doc.read_text(encoding="utf-8")
        for target in markdown_links(text):
            if re.match(r"^[a-z][a-z0-9+.-]*:", target):  # http:, https:, mailto:, ...
                continue
            path_part, _, anchor = target.partition("#")
            resolved = (doc.parent / path_part).resolve() if path_part else doc
            where = f"{doc.relative_to(docs_root.parent)}: {target}"
            if not resolved.exists():
                problems.append(f"{where} -> missing {path_part}")
                continue
            if anchor and resolved.is_file() and resolved.suffix == ".md":
                if anchor not in heading_anchors(resolved.read_text(encoding="utf-8")):
                    problems.append(f"{where} -> no heading for #{anchor}")
    return problems


def service_packages(services_dir: Path) -> list[str]:
    """Names of the backend service packages (directories with an __init__.py)."""
    return sorted(
        child.name
        for child in services_dir.iterdir()
        if child.is_dir() and not child.name.startswith("_") and (child / "__init__.py").is_file()
    )


def check_service_docs(docs_root: Path, services_dir: Path) -> list[str]:
    """Service packages missing their README.md or openapi.yaml under docs_agents/backend/services/."""
    problems = []
    for name in service_packages(services_dir):
        for required in ("README.md", "openapi.yaml"):
            path = docs_root / "backend" / "services" / name / required
            if not path.is_file():
                problems.append(f"{name}: missing {path.relative_to(docs_root.parent)}")
    return problems


def _registered_name(call: ast.Call) -> str | None:
    """The registered name when *call* is `<Factory>.register("name", cls)`, else None."""
    if not (isinstance(call.func, ast.Attribute) and call.func.attr == "register" and len(call.args) == 2):
        return None
    name, target = call.args
    if not (isinstance(name, ast.Constant) and isinstance(name.value, str)):
        return None
    return name.value if isinstance(target, ast.Name) and target.id == "cls" else None


def registered_providers(package_dir: Path) -> list[tuple[str, str]]:
    """(registered name, class name) for every `<Factory>.register("name", cls)` in providers/."""
    found = []
    providers_dir = package_dir / "providers"
    if not providers_dir.is_dir():
        return found
    for source in sorted(providers_dir.rglob("*.py")):
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        for cls in (node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)):
            for call in (node for node in ast.walk(cls) if isinstance(node, ast.Call)):
                registered = _registered_name(call)
                if registered is not None:
                    found.append((registered, cls.name))
    return found


def config_subdir(package_dir: Path) -> str | None:
    """The `config_subdir=` value of the package's ServiceAcceptCriteria, if it declares one."""
    init = package_dir / "__init__.py"
    tree = ast.parse(init.read_text(encoding="utf-8"), filename=str(init))
    for node in ast.walk(tree):
        if isinstance(node, ast.keyword) and node.arg == "config_subdir" and isinstance(node.value, ast.Constant):
            return str(node.value.value)
    return None


def check_provider_inventory(docs_root: Path, services_dir: Path, config_dir: Path) -> list[str]:
    """Provider classes and configuration files a service README fails to name."""
    problems = []
    for name in service_packages(services_dir):
        readme = docs_root / "backend" / "services" / name / "README.md"
        if not readme.is_file():
            continue  # reported by check_service_docs
        text = readme.read_text(encoding="utf-8")
        for registered, class_name in registered_providers(services_dir / name):
            if class_name not in text:
                problems.append(f"{name}: README does not name provider class {class_name} ({registered!r})")
        subdir = config_subdir(services_dir / name)
        if subdir and (config_dir / subdir).is_dir():
            for config_file in sorted((config_dir / subdir).glob("*.json")):
                if config_file.name not in text:
                    problems.append(f"{name}: README does not name configuration file {subdir}/{config_file.name}")
    return problems


def _iter_refs(node: Any) -> Iterator[str]:
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "$ref" and isinstance(value, str):
                yield value
            else:
                yield from _iter_refs(value)
    elif isinstance(node, list):
        for value in node:
            yield from _iter_refs(value)


def _path_params(parameters: Any) -> set[str]:
    return {
        str(param.get("name"))
        for param in parameters or []
        if isinstance(param, dict) and param.get("in") == "path"
    }


def _header_problems(document: dict) -> list[str]:
    problems = []
    version = document.get("openapi")
    if not (isinstance(version, str) and re.fullmatch(r"3\.\d+\.\d+", version)):
        problems.append(f"openapi must be a 3.x.y version string, got {version!r}")
    info = document.get("info")
    if not (isinstance(info, dict) and isinstance(info.get("title"), str) and isinstance(info.get("version"), str)):
        problems.append("info.title and info.version must be strings")
    if not isinstance(document.get("paths"), dict):
        problems.append("paths must be a mapping (use {} when there are no operations)")
    return problems


def _operation_problems(where: str, operation: Any, undeclared_candidates: set[str], schemes: dict) -> list[str]:
    """Problems in one operation; *undeclared_candidates* are template params not declared at path level."""
    if not isinstance(operation, dict):
        return [f"{where}: operation must be a mapping"]
    problems = []
    responses = operation.get("responses")
    if not isinstance(responses, dict) or not responses:
        problems.append(f"{where}: responses must be a non-empty mapping")
    missing = undeclared_candidates - _path_params(operation.get("parameters"))
    if missing:
        problems.append(f"{where}: path parameter(s) {sorted(missing)} not declared")
    problems.extend(
        f"{where}: unknown security scheme {scheme!r}"
        for requirement in operation.get("security") or []
        for scheme in requirement
        if scheme not in schemes
    )
    return problems


def _ref_problems(document: dict) -> list[str]:
    problems = []
    for ref in sorted(set(_iter_refs(document))):
        if not ref.startswith("#/"):
            problems.append(f"non-local $ref {ref!r}")
            continue
        node: Any = document
        for part in ref[2:].split("/"):
            part = part.replace("~1", "/").replace("~0", "~")
            if not isinstance(node, dict) or part not in node:
                problems.append(f"unresolved $ref {ref!r}")
                break
            node = node[part]
    return problems


def validate_openapi(document: Any) -> list[str]:
    """Structural OpenAPI 3.x problems in *document* (a parsed YAML/JSON mapping)."""
    if not isinstance(document, dict):
        return ["document is not a mapping"]
    problems = _header_problems(document)
    paths = document.get("paths") if isinstance(document.get("paths"), dict) else {}
    schemes = ((document.get("components") or {}).get("securitySchemes")) or {}
    operation_ids: Counter[str] = Counter()
    for path, item in paths.items():
        if not str(path).startswith("/"):
            problems.append(f"path {path!r} must start with '/'")
        if not isinstance(item, dict):
            problems.append(f"{path}: path item must be a mapping")
            continue
        undeclared = set(re.findall(r"\{([^}]+)\}", str(path))) - _path_params(item.get("parameters"))
        for method, operation in item.items():
            if method not in HTTP_METHODS:
                continue
            problems.extend(_operation_problems(f"{method.upper()} {path}", operation, undeclared, schemes))
            if isinstance(operation, dict) and operation.get("operationId"):
                operation_ids[str(operation["operationId"])] += 1
    problems.extend(f"duplicate operationId {op_id!r}" for op_id, count in operation_ids.items() if count > 1)
    return problems + _ref_problems(document)


def check_openapi(docs_root: Path) -> list[str]:
    """Structural problems in every openapi.yaml under *docs_root*."""
    problems = []
    for spec in sorted(docs_root.rglob("openapi.yaml")):
        where = spec.relative_to(docs_root.parent)
        try:
            document = yaml.safe_load(spec.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            problems.append(f"{where}: YAML parse error: {exc}")
            continue
        problems.extend(f"{where}: {problem}" for problem in validate_openapi(document))
    return problems


def tracked_readmes(repo_root: Path) -> list[str]:
    """Tracked README.md files outside vendored assets and docs_agents/ itself."""
    result = subprocess.run(["git", "ls-files"], cwd=repo_root, capture_output=True, text=True, check=True)
    return sorted(
        path for path in result.stdout.splitlines()
        if Path(path).name.lower() == "readme.md" and not path.startswith(README_INDEX_EXCLUDED_PREFIXES)
    )


def check_readme_index(docs_root: Path, readmes: list[str]) -> list[str]:
    """Pre-existing READMEs that docs_agents/README.md does not link."""
    index = docs_root / "README.md"
    if not index.is_file():
        return [f"missing {index.relative_to(docs_root.parent)}"]
    linked = {
        (index.parent / target.partition("#")[0]).resolve()
        for target in markdown_links(index.read_text(encoding="utf-8"))
        if target.partition("#")[0]
    }
    return [
        f"docs_agents/README.md does not link pre-existing {readme}"
        for readme in readmes
        if (docs_root.parent / readme).resolve() not in linked
    ]


def check_drift(repo_root: Path) -> list[str]:
    """Problems reported by `export_openapi.py --docs-agents --check` (imports the backend)."""
    result = subprocess.run(
        [sys.executable, str(repo_root / "scripts" / "export_openapi.py"), "--docs-agents", "--check"],
        cwd=repo_root, capture_output=True, text=True, check=False,
    )
    if result.returncode == 0:
        return []
    return [line for line in (result.stderr or result.stdout).splitlines() if line.strip()] or [
        f"export_openapi.py --docs-agents --check exited {result.returncode}",
    ]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    """Run every check, print PASS/FAIL per check, return the exit code."""
    parser = argparse.ArgumentParser(description="Validate docs_agents/ (read-only).")
    parser.add_argument("--skip-drift", action="store_true", help="skip the export comparison (no backend import)")
    args = parser.parse_args(argv)

    checks: list[tuple[str, Callable[[], list[str]]]] = [
        ("links", lambda: check_links(DOCS_ROOT)),
        ("services", lambda: check_service_docs(DOCS_ROOT, SERVICES_DIR)),
        ("providers", lambda: check_provider_inventory(DOCS_ROOT, SERVICES_DIR, CONFIG_DIR)),
        ("openapi", lambda: check_openapi(DOCS_ROOT)),
        ("readmes", lambda: check_readme_index(DOCS_ROOT, tracked_readmes(REPO_ROOT))),
    ]
    if not args.skip_drift:
        checks.append(("drift", lambda: check_drift(REPO_ROOT)))

    failed = 0
    for name, run in checks:
        problems = run()
        status = f"{GREEN}PASS{RESET}" if not problems else f"{RED}FAIL{RESET}"
        print(f"{BOLD}{name:10s}{RESET} {status}")
        for problem in problems:
            print(f"    {problem}")
        failed += bool(problems)
    print(f"\n{len(checks) - failed}/{len(checks)} checks passed.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
