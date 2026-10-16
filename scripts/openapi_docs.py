"""Per-owner OpenAPI documents for docs_agents/ (driven by `export_openapi.py --docs-agents`).

The default mode of scripts/export_openapi.py splits the runtime schema by hard-coded URL
prefixes into backend/src/services/*/openapi.yaml. This module splits the same runtime
schema (`app.openapi()`) by the module that defines each endpoint function instead:

    src.services.<package>[.*]   ->  docs_agents/backend/services/<package>/openapi.yaml
    src.api[.*] and src.main     ->  docs_agents/backend/api/openapi.yaml

Every operation in the schema must map to exactly one owner, otherwise generation fails:
an unowned or doubly owned operation means this mapping is wrong, and guessing from the
URL would hide that. A service package without routes still gets a valid document with
`paths: {}`.

Nothing here imports the backend. Callers pass the app's routes and schema in, so the
functions stay testable without FastAPI installed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator

import yaml

API_OWNER = "api"
HTTP_METHODS = ("get", "put", "post", "delete", "options", "head", "patch", "trace")
SCHEMA_REF_PREFIX = "#/components/schemas/"

GENERATED_HEADER = (
    "# GENERATED FILE - do not edit by hand.\n"
    "# Source: the runtime FastAPI schema (app.openapi()) of backend/src/main.py, split by the\n"
    "# module that defines each endpoint (see scripts/openapi_docs.py).\n"
    "# Regenerate: python scripts/export_openapi.py --docs-agents\n"
    "# Verify:     python scripts/export_openapi.py --docs-agents --check\n"
)

_YAML_OPTS: dict[str, Any] = {"default_flow_style": False, "allow_unicode": True, "sort_keys": False}


class OwnershipError(ValueError):
    """Raised when an operation has no owner, or more than one."""


@dataclass
class OperationIndex:
    """Who owns each (method, path) operation, and which modules each owner spans."""

    owners: dict[tuple[str, str], set[str]] = field(default_factory=dict)
    modules: dict[str, set[str]] = field(default_factory=dict)
    unmapped: list[str] = field(default_factory=list)


def discover_service_packages(services_dir: Path) -> list[str]:
    """Names of the service packages under backend/src/services (directories with an __init__.py)."""
    return sorted(
        child.name
        for child in services_dir.iterdir()
        if child.is_dir() and not child.name.startswith("_") and (child / "__init__.py").is_file()
    )


def owner_for_module(module: str, service_names: Iterable[str]) -> str | None:
    """Return the owner of an endpoint defined in *module*, or None when no rule covers it."""
    parts = module.split(".")
    if len(parts) >= 3 and parts[:2] == ["src", "services"] and parts[2] in set(service_names):
        return parts[2]
    if module == "src.main" or parts[:2] == ["src", "api"]:
        return API_OWNER
    return None


def iter_api_routes(routes: Iterable[Any]) -> Iterator[tuple[str, set[str], Any]]:
    """Yield (path, methods, endpoint) for each schema-visible route.

    Recent FastAPI versions keep included routers as nodes exposing
    `effective_route_contexts()`; older ones flatten them into APIRoute objects on
    `app.routes`. Both shapes are handled. Routes without an endpoint or methods (mounts)
    and routes excluded from the schema (/docs, /openapi.json) are skipped.
    """
    for route in routes:
        contexts = getattr(route, "effective_route_contexts", None)
        if callable(contexts):
            yield from iter_api_routes(contexts())
            continue
        endpoint = getattr(route, "endpoint", None)
        methods = getattr(route, "methods", None)
        path = getattr(route, "path", None)
        if endpoint is None or not methods or path is None or not getattr(route, "include_in_schema", False):
            continue
        yield path, set(methods), endpoint


def index_operations(routes: Iterable[Any], service_names: Iterable[str]) -> OperationIndex:
    """Map every (method, path) of *routes* to its owner(s)."""
    names = list(service_names)
    index = OperationIndex()
    for path, methods, endpoint in iter_api_routes(routes):
        module = getattr(endpoint, "__module__", "") or ""
        owner = owner_for_module(module, names)
        if owner is None:
            index.unmapped.append(f"{sorted(methods)} {path} (endpoint module {module!r})")
            continue
        index.modules.setdefault(owner, set()).add(module)
        for method in methods:
            index.owners.setdefault((method.lower(), path), set()).add(owner)
    return index


def partition_paths(schema: dict[str, Any], index: OperationIndex) -> dict[str, dict[str, dict[str, Any]]]:
    """Split schema["paths"] by owner, one operation at a time. Raises OwnershipError."""
    problems = list(f"no ownership rule for {entry}" for entry in index.unmapped)
    partitioned: dict[str, dict[str, dict[str, Any]]] = {}
    for path, item in (schema.get("paths") or {}).items():
        shared = {key: value for key, value in item.items() if key not in HTTP_METHODS}
        for method, operation in item.items():
            if method not in HTTP_METHODS:
                continue
            found = index.owners.get((method, path), set())
            if len(found) != 1:
                reason = "no owning route" if not found else f"owned by {sorted(found)}"
                problems.append(f"{method.upper()} {path}: {reason}")
                continue
            owner = next(iter(found))
            target = partitioned.setdefault(owner, {}).setdefault(path, dict(shared))
            target[method] = operation
    if problems:
        raise OwnershipError("\n".join(problems))
    return partitioned


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


def reachable_schemas(node: Any, all_schemas: dict[str, Any]) -> set[str]:
    """Component schema names referenced from *node*, followed transitively."""
    queue = [ref.removeprefix(SCHEMA_REF_PREFIX) for ref in _iter_refs(node) if ref.startswith(SCHEMA_REF_PREFIX)]
    seen: set[str] = set()
    while queue:
        name = queue.pop()
        if name in seen or name not in all_schemas:
            continue
        seen.add(name)
        queue.extend(
            ref.removeprefix(SCHEMA_REF_PREFIX)
            for ref in _iter_refs(all_schemas[name])
            if ref.startswith(SCHEMA_REF_PREFIX)
        )
    return seen


def _title(owner: str) -> str:
    if owner == API_OWNER:
        return "mx-admin backend API layer (src/api and src/main.py)"
    return f"mx-admin backend {owner} service HTTP API"


def build_document(
        schema: dict[str, Any], owner: str, paths: dict[str, dict[str, Any]], modules: Iterable[str],
) -> dict:
    """A complete OpenAPI document holding only *paths* and the components they need."""
    components = schema.get("components") or {}
    all_schemas = components.get("schemas") or {}
    used_schemas = reachable_schemas(paths, all_schemas)
    used_security = {
        name
        for item in paths.values()
        for method, operation in item.items()
        if method in HTTP_METHODS
        for requirement in operation.get("security") or []
        for name in requirement
    }
    module_list = sorted(modules)
    description = (
        "Operations whose endpoint functions are defined in: " + ", ".join(module_list) + "."
        if module_list
        else "This service package defines no HTTP endpoints."
    )
    document: dict[str, Any] = {
        "openapi": schema["openapi"],
        "info": {
            "title": _title(owner),
            "version": str((schema.get("info") or {}).get("version", "0.0.0")),
            "description": description,
        },
        "paths": {path: paths[path] for path in sorted(paths)},
    }
    picked: dict[str, Any] = {}
    security_schemes = components.get("securitySchemes") or {}
    if used_security:
        picked["securitySchemes"] = {name: security_schemes[name] for name in security_schemes if name in used_security}
    if used_schemas:
        picked["schemas"] = {name: all_schemas[name] for name in all_schemas if name in used_schemas}
    if picked:
        document["components"] = picked
    return document


def render(document: dict) -> str:
    """Serialize *document* deterministically, with the generated-file header."""
    return GENERATED_HEADER + yaml.dump(document, **_YAML_OPTS)


def output_path(docs_root: Path, owner: str) -> Path:
    """Where *owner*'s document lives under docs_agents/."""
    if owner == API_OWNER:
        return docs_root / "backend" / "api" / "openapi.yaml"
    return docs_root / "backend" / "services" / owner / "openapi.yaml"


def generate(
        schema: dict[str, Any], routes: Iterable[Any], service_names: list[str], docs_root: Path,
) -> dict[Path, str]:
    """Render one document per service package plus the API layer. Raises OwnershipError."""
    index = index_operations(routes, service_names)
    partitioned = partition_paths(schema, index)
    unknown = sorted(set(partitioned) - set(service_names) - {API_OWNER})
    if unknown:
        raise OwnershipError(f"operations owned by unknown owners: {unknown}")
    rendered: dict[Path, str] = {}
    for owner in [*service_names, API_OWNER]:
        document = build_document(schema, owner, partitioned.get(owner, {}), index.modules.get(owner, set()))
        rendered[output_path(docs_root, owner)] = render(document)
    return rendered


def count_operations(paths: dict[str, dict[str, Any]]) -> int:
    """Number of HTTP operations in a paths mapping."""
    return sum(1 for item in paths.values() for method in item if method in HTTP_METHODS)


def compare(rendered: dict[Path, str], docs_root: Path) -> list[str]:
    """Problems between *rendered* and the files on disk: missing, drifted or stale."""
    problems = []
    for path, text in sorted(rendered.items()):
        if not path.is_file():
            problems.append(f"missing: {path}")
        elif path.read_text(encoding="utf-8") != text:
            problems.append(f"drift: {path} differs from a fresh export")
    services_root = docs_root / "backend" / "services"
    if services_root.is_dir():
        for existing in sorted(services_root.glob("*/openapi.yaml")):
            if existing not in rendered:
                problems.append(f"stale: {existing} has no matching service package")
    return problems


def write(rendered: dict[Path, str]) -> None:
    """Write every rendered document, creating directories as needed."""
    for path, text in rendered.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
