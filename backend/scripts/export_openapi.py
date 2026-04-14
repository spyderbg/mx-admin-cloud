#!/usr/bin/env python3
"""Export FastAPI's auto-generated OpenAPI spec to docs/text_translation_openapi.yaml.

The spec is derived directly from route decorators and Pydantic models — no manual
editing required. Run this script after any change to routes or schemas.

Usage (from backend/):
    PYTHONPATH=. python scripts/export_openapi.py [--all]

Options:
    --all Export the full app spec (all routes) instead of text-translation only.
            Output: docs/openapi.yaml
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Path setup — allow running from backend/ without installing the package
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Provide the minimum env vars required by Settings so the app module loads
# without a real .env file (no server is started; only route metadata is read).
os.environ.setdefault("SECRET_KEY", "export-script-placeholder")
os.environ.setdefault("DB_PATH", str(ROOT / "data" / "users.db"))

# ---------------------------------------------------------------------------
# Import the FastAPI app (no lifespan runs; app.openapi() is synchronous)
# ---------------------------------------------------------------------------
from src.main import app  # noqa: E402 pylint: disable=wrong-import-position

try:
    import yaml  # PyYAML — already in requirements.txt via structlog/other deps
except ImportError:
    print("PyYAML is not installed. Run:  pip install pyyaml", file=sys.stderr)
    sys.exit(1)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
_TRANSLATION_PATH_PREFIXES = ("/internal/translat",)


def _used_schema_names(paths: dict) -> set[str]:
    """Collect all $ref names referenced under the given path subset."""
    import json
    text = json.dumps(paths)
    refs: set[str] = set()
    start = 0
    marker = '"$ref": "#/components/schemas/'
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
    """Recursively collect schema names reachable from *names* via $ref."""
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


def export_translation_only(full_schema: dict, output: Path) -> None:
    """Write a spec containing only text-translation paths and their schemas."""
    paths = {
        path: spec
        for path, spec in full_schema.get("paths", {}).items()
        if any(path.startswith(p) for p in _TRANSLATION_PATH_PREFIXES)
    }

    all_schemas = full_schema.get("components", {}).get("schemas", {})
    reachable = _collect_all_refs(_used_schema_names(paths), all_schemas)
    schemas = {k: v for k, v in all_schemas.items() if k in reachable}

    spec = {
        "openapi": full_schema["openapi"],
        "info": {
            **full_schema["info"],
            "title": "Text Translation Internal API",
        },
        "servers": full_schema.get("servers", []),
        "paths": paths,
        "components": {
            "securitySchemes": full_schema.get("components", {}).get("securitySchemes", {}),
            "schemas": schemas,
        },
    }

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as fh:
        yaml.dump(spec, fh, default_flow_style=False, allow_unicode=True, sort_keys=False)

    print(f"Wrote {len(paths)} path(s), {len(schemas)} schema(s) → {output}")


def export_all(full_schema: dict, output: Path) -> None:
    """Write the complete app spec."""
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as fh:
        yaml.dump(full_schema, fh, default_flow_style=False, allow_unicode=True, sort_keys=False)
    paths = full_schema.get("paths", {})
    schemas = full_schema.get("components", {}).get("schemas", {})
    print(f"Wrote {len(paths)} path(s), {len(schemas)} schema(s) → {output}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main() -> None:
    export_all_flag = "--all" in sys.argv
    full_schema = app.openapi()

    if export_all_flag:
        output = ROOT / "docs" / "openapi.yaml"
        export_all(full_schema, output)
    else:
        output = ROOT / "docs" / "text_translation_openapi.yaml"
        export_translation_only(full_schema, output)


if __name__ == "__main__":
    main()