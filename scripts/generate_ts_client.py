#!/usr/bin/env python3
"""
Generate a TypeScript API client and MSW handler stubs from a FastAPI routes.py file.

Usage:
    python generate_ts_client.py <service_name>

Example:
    python generate_ts_client.py content
    →  ../frontend/src/api/contentClient.ts
    →  ../frontend/src/mocks/contentHandlers.ts  (missing stubs only)
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

BACKEND_SERVICES_DIR = Path(__file__).resolve().parent.parent / "backend" / "src" / "services"
FRONTEND_API_DIR = Path(__file__).resolve().parent.parent / "frontend" / "src" / "api"
FRONTEND_TYPES_DIR = Path(__file__).resolve().parent.parent / "frontend" / "src" / "types"
FRONTEND_MOCKS_DIR = Path(__file__).resolve().parent.parent / "frontend" / "src" / "mocks"

API_V1_PREFIX = "/api/v1"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def snake_to_camel(name: str) -> str:
    """Convert snake_case to camelCase."""
    parts = name.split("_")
    return parts[0] + "".join(p.capitalize() for p in parts[1:])


def extract_router_prefix(tree: ast.Module) -> str:
    """Extract the path prefix from ``router = APIRouter(prefix=...)``.

    Strips f-string wrappers and ``settings.API_V1_STR`` to yield the
    effective relative prefix (e.g. ``/content``).
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "router":
                    if isinstance(node.value, ast.Call):
                        for kw in node.value.keywords:
                            if kw.arg == "prefix":
                                raw = _resolve_prefix_value(kw.value)
                                # Strip settings.API_V1_STR (which is /api/v1)
                                raw = raw.replace("{settings.API_V1_STR}", "")
                                raw = raw.replace("settings.API_V1_STR", "")
                                raw = re.sub(r"\{[^}]+\}", "", raw)  # any remaining f-string expr
                                return raw.rstrip("/")
    return ""


def _resolve_prefix_value(node: ast.AST) -> str:
    """Resolve a prefix expression to a plain string (best-effort)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for v in node.values:
            if isinstance(v, ast.Constant) and isinstance(v.value, str):
                parts.append(v.value)
            elif isinstance(v, ast.FormattedValue):
                # Capture e.g. {settings.API_V1_STR}
                if isinstance(v.value, ast.Attribute) and isinstance(v.value.value, ast.Name):
                    parts.append(f"{{{v.value.value.id}.{v.value.attr}}}")
                elif isinstance(v.value, ast.Name):
                    parts.append(f"{{{v.value.id}}}")
        return "".join(parts)
    return str(getattr(node, "s", "") if hasattr(node, "s") else "")


# ---------------------------------------------------------------------------
# Imports analysis
# ---------------------------------------------------------------------------

def collect_imported_schema_names(tree: ast.Module) -> set[str]:
    """Return names imported from any ``schemas`` module."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if "schemas" in mod:
                for alias in node.names:
                    names.add(alias.asname or alias.name)
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.asname or alias.name
                if "schemas" in name:
                    names.add(name)
    return names


def collect_path_constants(tree: ast.Module) -> dict[str, str]:
    """Return name -> value for module-level string constants (e.g. route path templates).

    routes.py defines paths as constants (``CATEGORY_IMAGES = "/categories/{category_id}/images"``)
    referenced by name in the decorator (``@router.get(CATEGORY_IMAGES)``) rather than as inline
    string literals, so decorator path arguments must be resolved through this table.
    """
    constants: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and isinstance(node.value, ast.Constant) \
                        and isinstance(node.value.value, str):
                    constants[target.id] = node.value.value
    return constants


def collect_annotated_aliases(tree: ast.Module) -> set[str]:
    """Return names assigned via ``X = Annotated[..., Depends(...)]``.

    These are dependency-injection aliases we must skip.
    """
    aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    if _is_annotated_depends(node.value):
                        aliases.add(target.id)
    return aliases


def _is_annotated_depends(node: ast.AST) -> bool:
    """Check if node is ``Annotated[..., Depends(...)]``."""
    if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and node.value.id == "Annotated":
        if isinstance(node.slice, ast.Tuple) and len(node.slice.elts) >= 2:
            last = node.slice.elts[-1]
            if isinstance(last, ast.Call) and isinstance(last.func, ast.Name) and last.func.id == "Depends":
                return True
    return False


# ---------------------------------------------------------------------------
# Route parsing
# ---------------------------------------------------------------------------

RouteInfo = dict[str, object]  # typed for internal use


def parse_function_body_raises_501(body: list[ast.stmt]) -> bool:
    """Returns True if the function body unconditionally raises 501."""
    for stmt in body:
        if isinstance(stmt, ast.Raise) and isinstance(stmt.exc, ast.Call):
            for kw in stmt.exc.keywords:
                if kw.arg == "status_code":
                    val = kw.value
                    # status.HTTP_501_NOT_IMPLEMENTED or direct 501 constant
                    if isinstance(val, ast.Constant) and val.value == 501:
                        return True
                    if isinstance(val, ast.Attribute) and "501" in (val.attr or ""):
                        return True
        if isinstance(stmt, ast.If):
            if parse_function_body_raises_501(stmt.body) and parse_function_body_raises_501(stmt.orelse or []):
                return True
    return False


def parse_routes(tree: ast.Module) -> list[dict]:
    """Parse all implemented (non-501) route functions from the AST."""
    schema_names = collect_imported_schema_names(tree)
    annotated_deps = collect_annotated_aliases(tree)
    prefix = extract_router_prefix(tree)
    path_constants = collect_path_constants(tree)

    routes: list[dict] = []

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue

        # --- Find router decorator ---
        route_dec = None
        for dec in node.decorator_list:
            if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute):
                if isinstance(dec.func.value, ast.Name) and dec.func.value.id == "router":
                    method = dec.func.attr.lower()
                    if method in ("get", "post", "put", "patch", "delete"):
                        route_dec = dec
                        break

        if route_dec is None:
            continue

        # --- Decode decorator arguments ---
        path = _extract_path(route_dec, path_constants)
        response_model = _extract_response_model(route_dec)
        summary = _extract_summary(route_dec)
        dec_status_code = _extract_status_code(route_dec)

        # --- Skip 501 stubs ---
        if dec_status_code == 501 or parse_function_body_raises_501(node.body):
            continue

        # --- Classify parameters ---
        body_param = None
        form_params = []
        path_params: list[tuple[str, str]] = []
        query_params: list[tuple[str, str, bool]] = []
        multipart = False

        for arg in node.args.args:
            arg_name = arg.arg
            if arg_name.startswith("_") and arg_name != "_user":
                continue

            # Resolve type annotation name
            ts_type, py_type = _resolve_param_type(arg.annotation, schema_names)

            # Check if it's a Depends (default value)
            if _is_depends_default(node.args, arg_name):
                continue
            if py_type in annotated_deps or arg_name in annotated_deps:
                continue

            # Check if it's in the path
            if _is_path_param(path, arg_name):
                path_params.append((arg_name, ts_type))
                continue

            # Check if it's a form/multipart parameter
            if _is_form_param(node.args, arg_name):
                form_params.append((arg_name, ts_type))
                multipart = True
                continue

            # Check if it's a body (Pydantic model)
            if py_type in schema_names:
                body_param = (arg_name, py_type)
                continue

            # Check if it has a File(...) default
            if _is_file_param(node.args, arg_name):
                multipart = True
                continue

            # Otherwise it's a query param — check if optional
            has_default = _arg_has_default(node.args, arg_name)
            query_params.append((arg_name, ts_type, has_default))

        routes.append({
            "method": method,
            "path": path,
            "prefix": prefix,
            "func_name": node.name,
            "summary": summary,
            "response_model": response_model,
            "body_param": body_param,
            "form_params": form_params,
            "path_params": path_params,
            "query_params": query_params,
            "multipart": multipart,
        })

    return routes


def _extract_path(dec: ast.Call, constants: dict[str, str] | None = None) -> str:
    if not dec.args:
        return "/"
    arg0 = dec.args[0]
    if isinstance(arg0, ast.Constant) and isinstance(arg0.value, str):
        return arg0.value
    if isinstance(arg0, ast.Name) and constants and arg0.id in constants:
        return constants[arg0.id]
    return "/"


def _extract_response_model(dec: ast.Call) -> str | None:
    for kw in dec.keywords:
        if kw.arg == "response_model":
            if isinstance(kw.value, ast.Name):
                return kw.value.id
            if isinstance(kw.value, ast.Subscript):
                sub = kw.value
                if (
                        isinstance(sub.value, ast.Name)
                        and sub.value.id in ("list", "List")
                        and isinstance(sub.slice, ast.Name)
                ):
                    return f"{sub.slice.id}[]"
            if isinstance(kw.value, ast.Constant) and kw.value.value is None:
                return None
    return None


def _extract_summary(dec: ast.Call) -> str | None:
    for kw in dec.keywords:
        if kw.arg == "summary" and isinstance(kw.value, ast.Constant):
            return str(kw.value.value)
    return None


def _extract_status_code(dec: ast.Call) -> int | None:
    for kw in dec.keywords:
        if kw.arg == "status_code":
            val = kw.value
            if isinstance(val, ast.Constant) and isinstance(val.value, int):
                return val.value
            # Could be status.HTTP_200_OK etc.
            if isinstance(val, ast.Attribute):
                if hasattr(val, "attr") and val.attr and val.attr.startswith("HTTP_"):
                    parts = val.attr.split("_")
                    if len(parts) >= 2:
                        try:
                            return int(parts[1])
                        except ValueError:
                            pass
    return None


PY_TS_TYPE_MAP: dict[str, str] = {
    "str": "string",
    "int": "number",
    "float": "number",
    "bool": "boolean",
    "Any": "any",
    "None": "null",
}


def _resolve_param_type(ann: ast.AST | None, schema_names: set[str]) -> tuple[str, str]:
    """Return (typescript_type, python_type_name)."""
    if ann is None:
        return ("unknown", "")

    # Simple name
    if isinstance(ann, ast.Name):
        name = ann.id
        ts = PY_TS_TYPE_MAP.get(name, name)
        return (ts, name)

    # Constant (string forward ref)
    if isinstance(ann, ast.Constant) and isinstance(ann.value, str):
        name = ann.value
        ts = PY_TS_TYPE_MAP.get(name, name)
        return (ts, name)

    # Optional[...] or ... | None
    if isinstance(ann, ast.Subscript) and isinstance(ann.value, ast.Name) and ann.value.id == "Optional":
        inner, _ = _resolve_param_type(ann.slice, schema_names)
        return (f"{inner} | null", "Optional")

    if isinstance(ann, ast.BinOp) and isinstance(ann.op, ast.BitOr):
        left, _ = _resolve_param_type(ann.left, schema_names)
        right, py_right = _resolve_param_type(ann.right, schema_names)
        if py_right in ("None", "NoneType"):
            return (f"{left} | null", left)
        return (f"{left} | {right}", f"{left} | {right}")

    # Subscript types like list[str], dict[str, X]
    if isinstance(ann, ast.Subscript):
        val, py_val = _resolve_param_type(ann.value, schema_names)
        slc = ann.slice
        if py_val in ("list", "List"):
            inner, _ = _resolve_param_type(slc, schema_names)
            return (f"{inner}[]", py_val)
        if py_val in ("dict", "Dict"):
            if isinstance(slc, ast.Tuple):
                _, key_py = _resolve_param_type(slc.elts[0], schema_names)
                inner, _ = _resolve_param_type(slc.elts[1], schema_names)
                return (f"Record<{key_py}, {inner}>", py_val)
            inner, _ = _resolve_param_type(slc, schema_names)
            return ("Record<string, any>", py_val)
        # Generic like Annotated[X, ...]
        return (val, py_val)

    # Attribute like status.HTTP_200_OK
    if isinstance(ann, ast.Attribute):
        return (ann.attr, ann.attr)

    return ("unknown", "")


def _is_path_param(path: str, arg_name: str) -> bool:
    return f"{{{arg_name}}}" in path


def _is_depends_default(args: ast.arguments, arg_name: str) -> bool:
    """Check if this arg has a ``Depends(...)`` default."""
    for default in args.defaults + args.kw_defaults:
        if isinstance(default, ast.Call) and isinstance(default.func, ast.Name) and default.func.id == "Depends":
            # Hard to correlate which arg → which default; do it by position
            pass
    # Match by position
    num_args = len(args.args)
    num_defaults = len(args.defaults)
    start = num_args - num_defaults
    for i in range(num_defaults):
        if args.args[start + i].arg == arg_name:
            return (
                isinstance(args.defaults[i], ast.Call)
                and isinstance(args.defaults[i].func, ast.Name)
                and args.defaults[i].func.id == "Depends"
            )
    return False


def _is_form_param(args: ast.arguments, arg_name: str) -> bool:
    """Check if this arg has a ``Form(...)`` default."""
    num_args = len(args.args)
    num_defaults = len(args.defaults)
    start = num_args - num_defaults
    for i in range(num_defaults):
        if args.args[start + i].arg == arg_name:
            default = args.defaults[i]
            if isinstance(default, ast.Call) and isinstance(default.func, ast.Name) and default.func.id == "Form":
                return True
    return False


def _is_file_param(args: ast.arguments, arg_name: str) -> bool:
    """Check if this arg has a ``File(...)`` default."""
    num_args = len(args.args)
    num_defaults = len(args.defaults)
    start = num_args - num_defaults
    for i in range(num_defaults):
        if args.args[start + i].arg == arg_name:
            default = args.defaults[i]
            if isinstance(default, ast.Call) and isinstance(default.func, ast.Name) and default.func.id == "File":
                return True
    return False


def _arg_has_default(args: ast.arguments, arg_name: str) -> bool:
    """Check if an argument has a default value."""
    num_args = len(args.args)
    num_defaults = len(args.defaults)
    start = num_args - num_defaults
    for i in range(num_defaults):
        if args.args[start + i].arg == arg_name:
            return True
    return False


# ---------------------------------------------------------------------------
# TypeScript code generation
# ---------------------------------------------------------------------------

def generate_ts(routes: list[dict], service_name: str) -> str:
    """Produce the TypeScript client source."""
    lines: list[str] = []
    schema_types: set[str] = set()
    schema_file = f"{snake_to_camel(service_name)}Schemas"

    # Collect schema types referenced
    for r in routes:
        rm = r.get("response_model")
        if rm:
            schema_types.add(str(rm).removesuffix("[]"))
        bp = r.get("body_param")
        if bp:
            schema_types.add(bp[1])
        # form types are not from schemas

    # Internal routers bypass the /api/v1 axios instance
    is_internal = bool(routes) and str(routes[0]["prefix"]).startswith("/internal")
    client_module = "./internalClient" if is_internal else "./client"

    lines.append("// Auto-generated – do not edit manually")
    lines.append("")
    lines.append(f"import client from '{client_module}'")

    if schema_types:
        sorted_types = sorted(schema_types, key=lambda x: x.lower())
        lines.append("import type {")
        for t in sorted_types:
            lines.append(f"  {t},")
        lines.append(f"}} from '../types/{schema_file}'")

    lines.append("")

    for r in routes:
        method: str = r["method"]
        path: str = r["path"]
        prefix: str = r["prefix"]
        func_name: str = r["func_name"]
        summary: str | None = r.get("summary")
        response_model: str | None = r.get("response_model")
        body_param: tuple | None = r.get("body_param")
        form_params: list = r.get("form_params", [])
        path_params: list = r.get("path_params", [])
        query_params: list = r.get("query_params", [])
        multipart: bool = r.get("multipart", False)

        # Function name
        ts_fn_name = snake_to_camel(func_name)

        # JSDoc
        if summary:
            lines.append(f"/** {summary} */")

        # Parameters
        func_params: list[str] = []

        # Path params (positional, required)
        for pname, ptype in path_params:
            func_params.append(f"{snake_to_camel(pname)}: {ptype}")

        # Body param (positional, required or optional)
        if multipart:
            func_params.append("formData: FormData")
        elif body_param:
            pname, ptype = body_param
            func_params.append(f"body: {ptype}")

        # Query params (bundled into optional params object)
        if query_params:
            q_entries: list[str] = []
            for qname, qtype, qopt in query_params:
                camel = snake_to_camel(qname)
                inner = f"{qtype} | undefined" if qopt else qtype
                q_entries.append(f"{camel}?: {inner}")
            inline = "; ".join(q_entries)
            func_params.append(f"params?: {{ {inline} }}")

        func_sig = ", ".join(func_params) if func_params else ""

        # Return type
        if response_model and response_model != "None":
            return_type = response_model
        else:
            return_type = "void"

        lines.append(f"export const {ts_fn_name} = async ({func_sig}): Promise<{return_type}> => {{")

        # Build URL
        url_path = prefix + path
        # Replace {param_name} with $}{camelCase(param_name)}
        url_expr = _build_url_expr(url_path, path_params)

        # Axios call
        if method == "get":
            if query_params:
                qobj = _build_query_obj(query_params)
                lines.append(f"  const r = await client.get<{return_type}>({url_expr}, {{ params: {qobj} }})")
            else:
                lines.append(f"  const r = await client.get<{return_type}>({url_expr})")
            lines.append("  return r.data")
        elif method == "delete":
            if query_params:
                qobj = _build_query_obj(query_params)
                lines.append(f"  const r = await client.delete<{return_type}>({url_expr}, {{ params: {qobj} }})")
                if return_type != "void":
                    lines.append("  return r.data")
            else:
                if return_type == "void":
                    lines.append(f"  await client.delete({url_expr})")
                else:
                    lines.append(f"  const r = await client.delete<{return_type}>({url_expr})")
                    lines.append("  return r.data")
        elif method in ("post", "put", "patch"):
            has_payload = bool(multipart or body_param)

            if multipart:
                # FormData
                call_parts = [url_expr]
                call_parts.append("formData")
                call_parts.append("{ headers: { 'Content-Type': 'multipart/form-data' } }")
                call = ", ".join(call_parts)
                lines.append(f"  const r = await client.{method}<{return_type}>({call})")
            elif body_param:
                call = f"{url_expr}, body"
                if query_params:
                    qobj = _build_query_obj(query_params)
                    call += f", {{ params: {qobj} }}"
                lines.append(f"  const r = await client.{method}<{return_type}>({call})")
            elif query_params:
                qobj = _build_query_obj(query_params)
                lines.append(f"  const r = await client.{method}<{return_type}>({url_expr}, undefined, {{ params: {qobj} }})")
            else:
                call = f"{url_expr}"
                lines.append(f"  const r = await client.{method}<{return_type}>({call})")

            if return_type != "void":
                lines.append("  return r.data")

        lines.append("}")
        lines.append("")

    return "\n".join(lines)


def _build_url_expr(url_path: str, path_params: list) -> str:
    """Build a JS template literal string for the URL."""
    if not path_params:
        return repr(url_path)

    has_params = any(f"{{{p[0]}}}" in url_path for p in path_params)
    if not has_params:
        return repr(url_path)

    # Replace path params
    expr = url_path
    for pname, _ in path_params:
        expr = expr.replace(f"{{{pname}}}", f"${{{snake_to_camel(pname)}}}")

    # Only use template literal if we have substitutions
    if "$" in expr:
        # Escape backticks
        expr = expr.replace("`", "\\`")
        return f"`{expr}`"

    return repr(url_path)


def _build_query_obj(query_params: list) -> str:
    """Build the axios params object literal with snake_case keys."""
    fields: list[str] = []
    for qname, _qtype, qopt in query_params:
        camel = snake_to_camel(qname)
        val = f"params?.{camel}"
        fields.append(f"{qname}: {val}")
    if not fields:
        return "undefined"
    single = ", ".join(fields)
    if len(single) < 100:
        return f"{{ {single} }}"
    return "{\n" + "\n".join(f"  {f}" for f in fields) + "\n}"


# ---------------------------------------------------------------------------
# MSW handler stub generation
# ---------------------------------------------------------------------------

def _fastapi_path_to_msw(prefix: str, route_path: str) -> str:
    """Convert a FastAPI route path to an MSW URL pattern.

    Example: prefix='/content', path='/categories/{name}' → '/api/v1/content/categories/:name'
    Internal routers (prefix '/internal/...') are served at the root, not under /api/v1.
    """
    base = "" if prefix.startswith("/internal") else API_V1_PREFIX
    full = base + prefix + route_path
    return re.sub(r"\{([^}]+)\}", r":\1", full)


def _parse_all_routes_for_handlers(tree: ast.Module) -> list[dict]:
    """Parse ALL routes (including 501 stubs) for MSW handler generation.

    Returns only the minimal fields needed: method, path, prefix, summary,
    is_501, dec_status_code.
    """
    prefix = extract_router_prefix(tree)
    path_constants = collect_path_constants(tree)
    routes: list[dict] = []

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue

        route_dec = None
        method = ""
        for dec in node.decorator_list:
            if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute):
                if isinstance(dec.func.value, ast.Name) and dec.func.value.id == "router":
                    m = dec.func.attr.lower()
                    if m in ("get", "post", "put", "patch", "delete"):
                        route_dec = dec
                        method = m
                        break

        if route_dec is None:
            continue

        path = _extract_path(route_dec, path_constants)
        summary = _extract_summary(route_dec)
        dec_status_code = _extract_status_code(route_dec)
        is_501 = dec_status_code == 501 or parse_function_body_raises_501(node.body)

        routes.append({
            "method": method,
            "path": path,
            "prefix": prefix,
            "summary": summary,
            "is_501": is_501,
            "dec_status_code": dec_status_code,
        })

    return routes


def _parse_existing_handler_keys(handlers_path: Path) -> set[tuple[str, str]]:
    """Return (method, msw_path) pairs that already have handlers in the file."""
    if not handlers_path.exists():
        return set()
    source = handlers_path.read_text()
    pattern = re.compile(r'\bhttp\.(get|post|put|patch|delete)\(\s*[\'"`]([^\'"`]+)[\'"`]')
    return {(m.group(1), m.group(2)) for m in pattern.finditer(source)}


def _stub_response(route: dict) -> str:
    """Return the MSW response expression for a stub handler."""
    if route["is_501"]:
        return "new HttpResponse(null, { status: 404 })"
    dec = route.get("dec_status_code")
    method = route["method"]
    if dec == 204 or (method == "delete" and dec not in (200, 201)):
        return "new HttpResponse(null, { status: 204 })"
    if dec == 201:
        return "HttpResponse.json({}, { status: 201 })"
    return "HttpResponse.json({})"


def _generate_handler_stubs(routes: list[dict]) -> list[str]:
    """Return formatted stub lines (without surrounding array brackets)."""
    lines: list[str] = []
    for r in routes:
        summary = r.get("summary") or ""
        if summary:
            lines.append(f"  // {summary}")
        msw_path = _fastapi_path_to_msw(r["prefix"], r["path"])
        stub = _stub_response(r)
        lines.append(f"  http.{r['method']}('{msw_path}', () => {stub}),")
        lines.append("")
    return lines


def update_handlers_file(service_name: str, all_routes: list[dict]) -> None:
    """Create or extend the MSW handlers file with stubs for missing routes.

    Never touches handlers that already exist in the file.
    """
    ts_name = snake_to_camel(service_name)
    handlers_path = FRONTEND_MOCKS_DIR / f"{ts_name}Handlers.ts"

    existing_keys = _parse_existing_handler_keys(handlers_path)

    missing = [
        r for r in all_routes
        if (_fastapi_path_to_msw(r["prefix"], r["path"]), r["method"]) not in
           {(path, m) for m, path in existing_keys}
    ]

    if not missing:
        print(f"✔  {handlers_path.name} up to date — no new stubs needed")
        return

    stub_lines = _generate_handler_stubs(missing)

    if not handlers_path.exists():
        FRONTEND_MOCKS_DIR.mkdir(parents=True, exist_ok=True)
        content = "\n".join([
            "// Auto-generated – do not edit manually",
            "",
            "import { http, HttpResponse } from 'msw'",
            "",
            f"export const {ts_name}Handlers = [",
            *stub_lines,
            "]",
            "",
        ])
        handlers_path.write_text(content)
        print(f"✔  Created \033[32m{handlers_path}\033[0m with {len(missing)} stubs")
    else:
        source = handlers_path.read_text()
        last_bracket = source.rfind("]")
        if last_bracket == -1:
            print(f"⚠  Could not find closing ] in {handlers_path.name}; skipping handler update")
            return
        insertion = "\n" + "\n".join(stub_lines)
        handlers_path.write_text(source[:last_bracket] + insertion + source[last_bracket:])
        print(f"✔  Added {len(missing)} stubs to \033[32m{handlers_path}\033[0m")

    for r in missing:
        msw_path = _fastapi_path_to_msw(r["prefix"], r["path"])
        tag = " [501→404]" if r["is_501"] else ""
        print(f"  {r['method'].upper():6s} {msw_path}{tag}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    service_name = sys.argv[1] if len(sys.argv) >= 2 else "content"
    routes_path = BACKEND_SERVICES_DIR / service_name / "routes.py"

    if not routes_path.exists():
        print(f"❌  routes.py not found: {routes_path}")
        sys.exit(1)

    source = routes_path.read_text()
    tree = ast.parse(source)

    # --- Generate TypeScript client (implemented routes only) ---
    routes = parse_routes(tree)

    if not routes:
        print(f"⚠   No implemented routes found in {routes_path}")
        sys.exit(0)

    ts_code = generate_ts(routes, service_name)

    FRONTEND_API_DIR.mkdir(parents=True, exist_ok=True)
    ts_name = snake_to_camel(service_name)
    out_path = FRONTEND_API_DIR / f"{ts_name}Client.ts"
    out_path.write_text(ts_code)
    print(f"✔  {len(routes)} routes → \033[32m{out_path}\033[0m")
    for r in routes:
        summary = r.get("summary") or ""
        label = f"  {r['method'].upper():6s} {r['prefix']}{r['path']}"
        if summary:
            label += f"  // {summary}"
        print(label)

    # --- Generate MSW handler stubs (all routes, missing only) ---
    print()
    all_routes = _parse_all_routes_for_handlers(tree)
    update_handlers_file(service_name, all_routes)


if __name__ == "__main__":
    main()
