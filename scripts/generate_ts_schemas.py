#!/usr/bin/env python3
"""
Generate TypeScript interfaces from Pydantic models defined in each
service's schemas.py.

For each service under ../backend/src/services/<service>/schemas.py,
a file ../frontend/src/types/<service>Schemas.ts is produced.
Shared schemas at services/schemas.py → sharedSchemas.ts.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

BACKEND_SERVICES_DIR = Path(__file__).resolve().parent.parent / "backend" / "src" / "services"
BACKEND_SRC_DIR = BACKEND_SERVICES_DIR.parent
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "frontend" / "src" / "types"

def snake_to_camel(name: str) -> str:
    """Convert snake_case to camelCase."""
    parts = name.split("_")
    return parts[0] + "".join(p.capitalize() for p in parts[1:])


BASE_TYPE_MAP: dict[str, str] = {
    "str": "string",
    "int": "number",
    "float": "number",
    "bool": "boolean",
    "Any": "any",
    "None": "null",
    "bytes": "string",
    "datetime": "string",
    "date": "string",
    "UUID": "string",
    "Path": "string",
    "Decimal": "number",
}


# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------

def _is_field_call(node: ast.AST) -> bool:
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "Field"


def _get_field_info(value_node: ast.AST | None) -> tuple[str | None, bool]:
    """Return (alias, is_optional) for a field assignment value.

    ``is_optional`` is ``True`` when the server accepts the field being absent
    (i.e. the Pydantic field has a default).
    """
    alias: str | None = None
    is_optional = False

    if value_node is None:
        return alias, is_optional

    if _is_field_call(value_node):
        for kw in value_node.keywords:
            if kw.arg == "alias" and isinstance(kw.value, ast.Constant):
                alias = str(kw.value.value)

        if value_node.args:
            first = value_node.args[0]
            is_optional = not (isinstance(first, ast.Constant) and first.value is ...)
        else:
            for kw in value_node.keywords:
                if kw.arg in ("default", "default_factory"):
                    is_optional = True
                    break
    else:
        is_optional = not _is_ellipsis(value_node)

    return alias, is_optional


def _is_ellipsis(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and node.value is ...


def _resolve_type(node: ast.AST | None, local_models: set[str], local_enums: set[str]) -> str:
    if node is None:
        return "unknown"

    if isinstance(node, ast.Name):
        name = node.id
        if name in BASE_TYPE_MAP:
            return BASE_TYPE_MAP[name]
        return name

    if isinstance(node, ast.Constant):
        if node.value is None:
            return "null"
        if isinstance(node.value, str):
            return node.value

    if isinstance(node, ast.Attribute):
        return f"{_resolve_type(node.value, local_models, local_enums)}.{node.attr}"

    if isinstance(node, ast.Subscript):
        value = _resolve_type(node.value, local_models, local_enums)
        slc = node.slice

        if value in ("list", "List"):
            return f"{_resolve_type(slc, local_models, local_enums)}[]"

        if value in ("dict", "Dict"):
            if isinstance(slc, ast.Tuple):
                key = _resolve_type(slc.elts[0], local_models, local_enums)
                val = _resolve_type(slc.elts[1], local_models, local_enums)
            else:
                key = "string"
                val = _resolve_type(slc, local_models, local_enums)
            return f"Record<{key}, {val}>"

        if value == "Optional":
            return f"{_resolve_type(slc, local_models, local_enums)} | null"

        if value == "Union":
            parts = (
                [_resolve_type(e, local_models, local_enums) for e in slc.elts]
                if isinstance(slc, ast.Tuple)
                else [_resolve_type(slc, local_models, local_enums)]
            )
            return " | ".join(parts)

        if isinstance(slc, ast.Tuple):
            params = ", ".join(_resolve_type(e, local_models, local_enums) for e in slc.elts)
            return f"{value}<{params}>"
        return f"{value}<{_resolve_type(slc, local_models, local_enums)}>"

    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        left = _resolve_type(node.left, local_models, local_enums)
        right = _resolve_type(node.right, local_models, local_enums)
        return f"{left} | {right}"

    return "unknown"


def _get_docstring(body: list[ast.stmt]) -> str | None:
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        val = body[0].value.value
        if isinstance(val, str):
            return val.strip()
    return None


def _clean_doc(doc: str | None) -> str | None:
    if not doc:
        return None
    lines = [l for l in doc.split("\n")]
    # Strip leading/trailing empty lines
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines) if len(lines) > 1 else (lines[0].strip() if lines else None)


# ---------------------------------------------------------------------------
# Enum extraction
# ---------------------------------------------------------------------------

def _extract_enum_members(body: list[ast.stmt]) -> list[tuple[str, str]]:
    members: list[tuple[str, str]] = []
    for item in body:
        if isinstance(item, ast.Assign):
            for target in item.targets:
                if isinstance(target, ast.Name) and target.id.isupper():
                    if isinstance(item.value, ast.Constant):
                        members.append((target.id, str(item.value.value)))
        elif isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
            name = item.target.id
            if name.isupper() and isinstance(item.value, ast.Constant):
                members.append((name, str(item.value.value)))
    return members


def _module_path(module: str) -> Path | None:
    """Resolve a dotted ``src.*`` module name onto its file, when it exists."""
    if not module.startswith("src."):
        return None
    candidate = BACKEND_SRC_DIR.joinpath(*module.split(".")[1:]).with_suffix(".py")
    return candidate if candidate.is_file() else None


def _imported_enums(tree: ast.AST) -> dict[str, dict]:
    """Return enum definitions imported by a schemas module.

    Enums are frequently declared in a service's ``models.py`` and merely
    imported into ``schemas.py`` (e.g. ``LogLevel``). Without this, the emitted
    TypeScript references a type it never declares.
    """
    wanted: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                if alias.asname is None:
                    wanted[alias.name] = node.module

    found: dict[str, dict] = {}
    for name, module in wanted.items():
        path = _module_path(module)
        if path is None:
            continue
        try:
            module_tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(module_tree):
            if not isinstance(node, ast.ClassDef) or node.name != name:
                continue
            bases = {b.id for b in node.bases if isinstance(b, ast.Name)}
            if "Enum" not in bases:
                continue
            members = _extract_enum_members(node.body)
            if members:
                found[name] = {"name": name, "members": members, "doc": _get_docstring(node.body)}
    return found


# ---------------------------------------------------------------------------
# Service discovery
# ---------------------------------------------------------------------------

def _discover_services() -> list[tuple[str, Path]]:
    """Return list of (service_name, schemas_path) tuples."""
    results: list[tuple[str, Path]] = []
    shared = BACKEND_SERVICES_DIR / "schemas.py"
    if shared.exists():
        results.append(("shared", shared))
    for child in sorted(BACKEND_SERVICES_DIR.iterdir()):
        if child.is_dir():
            schema = child / "schemas.py"
            if schema.exists():
                results.append((child.name, schema))
    return results


# ---------------------------------------------------------------------------
# Single-file parser
# ---------------------------------------------------------------------------

def _parse_schemas(source: str) -> tuple[dict, dict]:
    """Parse a schemas.py source and return ``(models, enums)`` dicts.

    Each model entry::

        {"name": str, "parent": str | None, "fields": list[Field], "doc": str | None}

    where ``Field`` = ``(ts_name, ts_type, is_optional)``.

    Each enum entry::

        {"name": str, "members": list[(name, value)], "doc": str | None}
    """
    tree = ast.parse(source)

    # Collect all ClassDef nodes
    class_defs: dict[str, ast.ClassDef] = {
        node.name: node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)
    }

    model_names: set[str] = set()
    enum_names: set[str] = set()

    # Identify direct BaseModel / Enum subclasses
    for name, node in class_defs.items():
        bases = {b.id for b in node.bases if isinstance(b, ast.Name)}
        if "BaseModel" in bases:
            model_names.add(name)
        if "Enum" in bases:
            enum_names.add(name)

    # Expand: detect indirect BaseModel subclasses (inherit from another model)
    changed = True
    while changed:
        changed = False
        for name, node in class_defs.items():
            if name in model_names or name in enum_names:
                continue
            bases = {b.id for b in node.bases if isinstance(b, ast.Name)}
            if bases & model_names:
                model_names.add(name)
                changed = True

    models: dict[str, dict] = {}
    enums: dict[str, dict] = {}

    # Build enum info
    for name in enum_names:
        node = class_defs[name]
        doc = _get_docstring(node.body)
        members = _extract_enum_members(node.body)
        enums[name] = {"name": name, "members": members, "doc": doc}

    # Enums declared elsewhere (typically the service's models.py) still need a
    # TypeScript declaration when a schema field references them.
    external_enums = _imported_enums(tree)
    enum_names |= set(external_enums)

    # Build model info
    for name in model_names:
        node = class_defs[name]
        doc = _get_docstring(node.body)

        # Determine parent model (first base that is also a model)
        parent: str | None = None
        for b in node.bases:
            if isinstance(b, ast.Name) and b.id in model_names and b.id != name:
                parent = b.id
                break

        fields: list[tuple[str, str, bool]] = []
        for item in node.body:
            if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                field_name = item.target.id
                if field_name.startswith("_"):
                    continue

                alias, is_optional = _get_field_info(item.value)
                ts_name = alias if alias else field_name
                ts_type = _resolve_type(item.annotation, model_names, enum_names)
                fields.append((ts_name, ts_type, is_optional))

        models[name] = {"name": name, "parent": parent, "fields": fields, "doc": doc}

    referenced = {ts_type for model in models.values() for _, ts_type, _ in model["fields"]}
    for name, info in external_enums.items():
        if any(name == part.strip() for value in referenced for part in re.split(r"[|\[\]<>, ]", value)):
            enums[name] = info

    return models, enums


# ---------------------------------------------------------------------------
# Code generator
# ---------------------------------------------------------------------------

def _generate_ts(models: dict, enums: dict) -> str:
    lines: list[str] = []

    # Enums
    for e in sorted(enums.values(), key=lambda x: x["name"]):
        doc = _clean_doc(e["doc"])
        if doc:
            lines.append(f"/** {doc} */")
        values = " | ".join(f"'{v}'" for _, v in e["members"])
        lines.append(f"export type {e['name']} = {values}")
        lines.append("")

    # Interfaces
    for m in sorted(models.values(), key=lambda x: x["name"]):
        doc = _clean_doc(m["doc"])
        if doc:
            lines.append(f"/** {doc} */")

        extends = f" extends {m['parent']}" if m["parent"] else ""
        lines.append(f"export interface {m['name']}{extends} {{")

        for ts_name, ts_type, is_optional in m["fields"]:
            opt = "?" if is_optional else ""
            lines.append(f"    {ts_name}{opt}: {ts_type}")

        lines.append("}")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    header = "// Auto-generated – do not edit manually\n"

    for service_name, schema_path in _discover_services():
        source = schema_path.read_text()
        models, enums = _parse_schemas(source)
        ts_code = _generate_ts(models, enums)

        imports = ""
        if "LocalizedString" in ts_code:
            imports = "import { LocalizedString } from './localization'\n\n"
        body = header + imports + ts_code
        ts_name = snake_to_camel(service_name)
        out_path = OUTPUT_DIR / f"{ts_name}Schemas.ts"
        out_path.write_text(body)
        print(f"✔ {service_name:20s} → {out_path.name}")


if __name__ == "__main__":
    main()
