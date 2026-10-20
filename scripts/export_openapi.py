#!/usr/bin/env python3
"""Export FastAPI's auto-generated OpenAPI spec to per-service YAML files under docs/.

The spec is derived directly from route decorators and Pydantic models — no manual
editing required. Run this script after any change to routes or schemas.

Usage:
    python scripts/export_openapi.py # all-services
    python scripts/export_openapi.py --service translation # one service
    python scripts/export_openapi.py --service logging
    python scripts/export_openapi.py --all # full app spec
    python scripts/export_openapi.py --docs-agents # docs_agents/backend/**/openapi.yaml
    python scripts/export_openapi.py --docs-agents --check # verify them, write nothing

Options:
    --service <name> Export only the named service (see SERVICES below).
    --all Export the full app spec (all routes). Output: docs/openapi.yaml
    --docs-agents Export one spec per service package (plus the API layer) into docs_agents/,
                  owned by endpoint module rather than URL prefix (see scripts/openapi_docs.py).
    --check With --docs-agents: exit 1 if the files on disk differ from a fresh export.
"""

from __future__ import annotations

import inspect
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Path setup — allow running from project root without installing the package
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent  # Project root: mx-admin/
BACKEND_ROOT = ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

# Minimum env vars so Settings loads without a real .env file.
# No server is started; app.openapi() is a synchronous introspection call.
os.environ.setdefault("SECRET_KEY", "export-script-placeholder")
os.environ.setdefault("DB_PATH", str(BACKEND_ROOT / "data" / "users.db"))

from src.main import app  # noqa: E402 pylint: disable=wrong-import-position

try:
    import yaml
except ImportError:
    print("PyYAML is not installed. Run:  pip install pyyaml", file=sys.stderr)
    sys.exit(1)

# ---------------------------------------------------------------------------
# Service registry — add a new entry here to support additional services
# ---------------------------------------------------------------------------
@dataclass
class ServiceSpec:
    title: str
    path_prefixes: tuple[str, ...]
    output: Path
    tags: list[str] = field(default_factory=list)


SERVICES: dict[str, ServiceSpec] = {
    "translation": ServiceSpec(
        title="Text Translation Internal API",
        path_prefixes=("/internal/translat",),
        output=BACKEND_ROOT / "src" / "services" / "text_translation" / "openapi.yaml",
    ),
    "logging": ServiceSpec(
        title="Logging Internal API",
        path_prefixes=("/internal/log",),
        output=BACKEND_ROOT / "src" / "services" / "logging" / "openapi.yaml",
    ),
    "tts": ServiceSpec(
        title="Text to Speech Internal API",
        path_prefixes=("/internal/generators",),
        output=BACKEND_ROOT / "src" / "services" / "text_to_speech" / "openapi.yaml",
    ),
    "text_to_image": ServiceSpec(
        title="Text to Image Internal API",
        path_prefixes=("/internal/image-generators",),
        output=BACKEND_ROOT / "src" / "services" / "text_to_image" / "openapi.yaml",
    ),
    "configuration": ServiceSpec(
        title="Configuration Internal API",
        path_prefixes=("/internal/configurations",),
        output=BACKEND_ROOT / "src" / "services" / "configuration" / "openapi.yaml",
    ),
    "content": ServiceSpec(
        title="Content Management API",
        path_prefixes=("/api/v1/content",),
        output=BACKEND_ROOT / "src" / "services" / "content" / "openapi.yaml",
    ),
    "voice_optimization": ServiceSpec(
        title="Voice Optimization Internal API",
        path_prefixes=("/internal/optimizers",),
        output=BACKEND_ROOT / "src" / "services" / "voice_optimization" / "openapi.yaml",
    ),
}

# ---------------------------------------------------------------------------
# Source-file discovery
# ---------------------------------------------------------------------------

def _source_files_for_service(spec: ServiceSpec, schema_names: set[str]) -> list[Path]:
    """Return sorted relative paths of every source file that contributes to *spec*.

    Covers two categories:
    - Route files — the .py file that defines each matching endpoint function.
    - Schema files — the .py file that defines each Pydantic model class used
                     by those endpoints (found by name in already-loaded modules).
    """
    from fastapi.routing import APIRoute  # pylint: disable=import-outside-toplevel

    files: set[Path] = set()

    # Route endpoint files
    for route in app.routes:
        if isinstance(route, APIRoute) and any(
            route.path.startswith(p) for p in spec.path_prefixes
        ):
            try:
                files.add(Path(inspect.getfile(route.endpoint)))
            except (TypeError, OSError):
                pass

    # Pydantic model (schema) files — search all loaded modules by class name
    for module in sys.modules.values():
        if module is None:
            continue
        for attr_name, obj in vars(module).items():
            if attr_name in schema_names and isinstance(obj, type):
                try:
                    files.add(Path(inspect.getfile(obj)))
                except (TypeError, OSError):
                    pass

    # Keep only project-owned files (under BACKEND_ROOT); skip site-packages and extensions
    result = []
    for f in sorted(files):
        try:
            result.append(f.relative_to(BACKEND_ROOT))
        except ValueError:
            pass  # outside the project tree — framework/library file, skip
    return result


# ---------------------------------------------------------------------------
# $ref crawler helpers
# ---------------------------------------------------------------------------

def _used_schema_names(obj: dict) -> set[str]:
    """Return all schema names referenced via $ref inside *obj* (JSON-serialised)."""
    import json
    text = json.dumps(obj)
    refs: set[str] = set()
    marker = '"$ref": "#/components/schemas/'
    start = 0
    while True:
        idx = text.find(marker, start)
        if idx == -1:
            break
        begin = idx + len(marker)
        end = text.index('"', begin)
        refs.add(text[begin:end])
        start = end
    return refs


def _collect_all_refs(names: set[str], all_schemas: dict) -> set[str]:
    """Transitively collect every schema name reachable from *names* via $ref."""
    visited: set[str] = set()
    queue = list(names)
    while queue:
        name = queue.pop()
        if name in visited or name not in all_schemas:
            continue
        visited.add(name)
        for ref_name in _used_schema_names(all_schemas[name]):
            if ref_name not in visited:
                queue.append(ref_name)
    return visited

# ---------------------------------------------------------------------------
# Export helpers
# ---------------------------------------------------------------------------

_YAML_OPTS: dict = dict(default_flow_style=False, allow_unicode=True, sort_keys=False)
_SEP_WIDTH = 70


def _tag_separator(label: str) -> str:
    prefix = f"# ─── {label} "
    return prefix + "─" * max(4, _SEP_WIDTH - len(prefix))


def _first_tag(path_item: dict) -> str:
    for method in ("get", "post", "put", "patch", "delete", "head", "options"):
        tags = path_item.get(method, {}).get("tags", [])
        if tags:
            return tags[0]
    return ""


def _paths_yaml(paths: dict) -> str:
    """Serialize the paths dict with tag-based comment separators."""
    lines: list[str] = ["paths:"]
    current_tag: str | None = None
    sorted_items = sorted(paths.items(), key=lambda kv: (not (t := _first_tag(kv[1])), t, kv[0]))
    for path, defn in sorted_items:
        tag = _first_tag(defn)
        if tag and tag != current_tag:
            lines.append("")
            lines.append(f"  {_tag_separator(tag)}")
            current_tag = tag
        for line in yaml.dump({path: defn}, **_YAML_OPTS).splitlines():
            lines.append(("  " + line) if line.strip() else "")
    return "\n".join(lines) + "\n"


def export_service(spec: ServiceSpec, full_schema: dict) -> None:
    """Write a focused spec for one service (filtered paths + reachable schemas)."""
    paths = {
        path: defn
        for path, defn in full_schema.get("paths", {}).items()
        if any(path.startswith(p) for p in spec.path_prefixes)
    }

    all_schemas = full_schema.get("components", {}).get("schemas", {})
    reachable = _collect_all_refs(_used_schema_names(paths), all_schemas)
    schemas = {k: v for k, v in all_schemas.items() if k in reachable}

    header = {
        "openapi": full_schema["openapi"],
        "info": {**full_schema["info"], "title": spec.title},
        "servers": full_schema.get("servers", []),
        "components": {
            "securitySchemes": full_schema.get("components", {}).get("securitySchemes", {}),
            "schemas": schemas,
        },
    }

    spec.output.parent.mkdir(parents=True, exist_ok=True)
    with spec.output.open("w", encoding="utf-8") as fh:
        fh.write(yaml.dump(header, **_YAML_OPTS))
        fh.write("\n")
        fh.write(_paths_yaml(paths))

    source_files = _source_files_for_service(spec, reachable)
    try:
        output_rel = spec.output.relative_to(BACKEND_ROOT)
    except ValueError:
        output_rel = spec.output

    print(f"[{spec.title}]  {len(paths)} path(s), {len(schemas)} schema(s)")
    print(f"  Source files ({len(source_files)}):")
    for f in source_files:
        print(f"    {f}")
    print(f"  Output file:")
    print(f"    {output_rel}")
    print()


def export_all(full_schema: dict) -> None:
    """Write the complete app spec to docs/openapi.yaml."""
    output = BACKEND_ROOT / "docs" / "openapi.yaml"
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as fh:
        yaml.dump(full_schema, fh, **_YAML_OPTS)
    paths = full_schema.get("paths", {})
    schemas = full_schema.get("components", {}).get("schemas", {})
    print(f"[Full app]  {len(paths)} path(s), {len(schemas)} schema(s) → {output}")


def export_docs_agents(full_schema: dict, *, check: bool) -> int:
    """Write (or, with *check*, only verify) docs_agents/backend/**/openapi.yaml. Returns an exit code."""
    import openapi_docs  # pylint: disable=import-outside-toplevel  # scripts/ sits on sys.path when run directly

    docs_root = ROOT / "docs_agents"
    service_names = openapi_docs.discover_service_packages(BACKEND_ROOT / "src" / "services")
    try:
        rendered = openapi_docs.generate(full_schema, app.routes, service_names, docs_root)
    except openapi_docs.OwnershipError as exc:
        print(f"Operation ownership is incomplete or ambiguous:\n{exc}", file=sys.stderr)
        return 1

    owned = sum(
        openapi_docs.count_operations(yaml.safe_load(text).get("paths") or {}) for text in rendered.values()
    )
    total = openapi_docs.count_operations(full_schema.get("paths", {}))
    if owned != total:
        print(f"Exported {owned} operation(s), but the app has {total}.", file=sys.stderr)
        return 1

    if check:
        problems = openapi_docs.compare(rendered, docs_root)
        for problem in problems:
            print(problem, file=sys.stderr)
        print(f"[docs_agents]  {len(rendered)} document(s), {total} operation(s): "
              + ("up to date" if not problems else f"{len(problems)} problem(s)"))
        return 1 if problems else 0

    openapi_docs.write(rendered)
    for path, text in sorted(rendered.items()):
        operations = openapi_docs.count_operations(yaml.safe_load(text).get("paths") or {})
        print(f"  {operations:3d} operation(s) → {path.relative_to(ROOT)}")
    print(f"[docs_agents]  {len(rendered)} document(s), {total} operation(s)")
    return 0

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    args = sys.argv[1:]
    full_schema = app.openapi()

    if "--docs-agents" in args:
        sys.exit(export_docs_agents(full_schema, check="--check" in args))

    if "--all" in args:
        export_all(full_schema)
        return

    service_flag_idx = args.index("--service") if "--service" in args else -1
    if service_flag_idx != -1:
        try:
            name = args[service_flag_idx + 1]
        except IndexError:
            print("--service requires a name argument", file=sys.stderr)
            sys.exit(1)
        if name not in SERVICES:
            print(f"Unknown service '{name}'. Available: {', '.join(SERVICES)}", file=sys.stderr)
            sys.exit(1)
        export_service(SERVICES[name], full_schema)
        return

    # Default: export all registered services
    for svc in SERVICES.values():
        export_service(svc, full_schema)


if __name__ == "__main__":
    main()
