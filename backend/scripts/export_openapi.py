#!/usr/bin/env python3
"""Export FastAPI's auto-generated OpenAPI spec to per-service YAML files under docs/.

The spec is derived directly from route decorators and Pydantic models — no manual
editing required. Run this script after any change to routes or schemas.

Usage (from backend/):
    PYTHONPATH=. python scripts/export_openapi.py # all services
    PYTHONPATH=. python scripts/export_openapi.py --service translation # one service
    PYTHONPATH=. python scripts/export_openapi.py --service logging
    PYTHONPATH=. python scripts/export_openapi.py --all # full app spec

Options:
    --service <name> Export only the named service (see SERVICES below).
    --all Export the full app spec (all routes). Output: docs/openapi.yaml
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Path setup — allow running from backend/ without installing the package
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Minimum env vars so Settings loads without a real .env file.
# No server is started; app.openapi() is a synchronous introspection call.
os.environ.setdefault("SECRET_KEY", "export-script-placeholder")
os.environ.setdefault("DB_PATH", str(ROOT / "data" / "users.db"))

from src.main import app  # noqa: E402  pylint: disable=wrong-import-position

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
        output=ROOT / "docs" / "text_translation_openapi.yaml",
    ),
    "logging": ServiceSpec(
        title="Logging Internal API",
        path_prefixes=("/internal/logging",),
        output=ROOT / "docs" / "logging_openapi.yaml",
    ),
    "tts": ServiceSpec(
        title="Text to Speech Internal API",
        path_prefixes=("/internal/generators",),
        output=ROOT / "docs" / "text_to_speech_openapi.yaml",
    ),
}

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

    out: dict = {
        "openapi": full_schema["openapi"],
        "info": {**full_schema["info"], "title": spec.title},
        "servers": full_schema.get("servers", []),
        "paths": paths,
        "components": {
            "securitySchemes": full_schema.get("components", {}).get("securitySchemes", {}),
            "schemas": schemas,
        },
    }

    spec.output.parent.mkdir(parents=True, exist_ok=True)
    with spec.output.open("w", encoding="utf-8") as fh:
        yaml.dump(out, fh, default_flow_style=False, allow_unicode=True, sort_keys=False)

    print(f"[{spec.title}]  {len(paths)} path(s), {len(schemas)} schema(s) → {spec.output}")


def export_all(full_schema: dict) -> None:
    """Write the complete app spec to docs/openapi.yaml."""
    output = ROOT / "docs" / "openapi.yaml"
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as fh:
        yaml.dump(full_schema, fh, default_flow_style=False, allow_unicode=True, sort_keys=False)
    paths = full_schema.get("paths", {})
    schemas = full_schema.get("components", {}).get("schemas", {})
    print(f"[Full app]  {len(paths)} path(s), {len(schemas)} schema(s) → {output}")

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    args = sys.argv[1:]
    full_schema = app.openapi()

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
