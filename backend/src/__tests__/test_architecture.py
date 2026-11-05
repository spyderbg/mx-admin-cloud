"""Architecture rules for backend/src: service encapsulation and provider encapsulation.

The rules are documented in backend/CLAUDE.md ("Service and provider encapsulation"):

1. Code outside a service reaches it only through its package facade (``src.services.<svc>``), never
   through a submodule such as ``src.services.<svc>.service`` or ``src.services.<svc>.providers.<name>``.
2. A concrete provider never imports a sibling provider, and ``_``-prefixed helper modules shared by
   several providers never import a concrete provider.
3. Every concrete provider is its own package under ``src/services/<svc>/providers/``, and providers are
   registered with their factory only from inside their package.
4. A service facade declares ``__all__``, every listed name exists, and none is a provider implementation.
5. The import-time dependency graph between services has no cycles.
6. A service's own code selects its providers through its factory: outside ``providers/`` (and tests) it never
   imports a concrete provider package, only the provider loader ``src.services.<svc>.providers``.

Sources are read with ``ast``; nothing is imported, so a violation is reported even where the import
would only fail at runtime. Imports are resolved like Python does (relative imports, ``from pkg import
submodule``, aliases) and attribute access through a bound module is followed, so
``from src.services import text_to_speech`` + ``text_to_speech.service.X`` is caught too.
Name binding is tracked per module (shadowing inside functions is not modelled).

Run: ``cd backend && AUTH_DISABLED=false PYTHONPATH="$PWD/src" ../.venv/bin/python -m pytest src/__tests__``
"""

# pylint: disable=missing-function-docstring

from __future__ import annotations

import ast
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1]

# Deliberate exceptions to rules 1 and 6 — (importing file relative to src/, predicate on the imported module)
# and the reason. Keep this list short; every entry is also listed in backend/CLAUDE.md.
ALLOWED_DEEP_IMPORTS: list[tuple[str, str, str]] = [
    ("main.py", "src.services.*.routes",
     "the application entry point mounts each service's router module"),
    ("core/log_client.py", "src.services.logging.routes",
     "the logging HTTP client uses the route paths defined next to the logging router"),
    ("services/configuration/__init__.py", "src.services.configuration.providers.file_system",
     "the file-system configuration provider is built at runtime from the registry builder, "
     "which no configuration file can supply"),
]


@dataclass(frozen=True)
class Import:
    """One module reached from a file, by an import statement or by attribute access."""

    line: int
    target: str  # deepest existing module that is reached
    import_time: bool  # False inside functions and ``if TYPE_CHECKING:`` blocks
    bound_name: str | None = None  # module-level name the statement binds (imports only)


class Codebase:
    """Static view of a ``src/`` tree: modules, services and the imports of each file."""

    def __init__(self, src: Path) -> None:
        self.src = src
        self.services = sorted(
            p.name for p in (src / "services").iterdir()
            if p.is_dir() and (p / "__init__.py").is_file() and not p.name.startswith("_")
        )

    def files(self) -> list[Path]:
        return sorted(p for p in self.src.rglob("*.py") if "__pycache__" not in p.parts)

    def rel(self, path: Path) -> str:
        return path.relative_to(self.src).as_posix()

    def module_of(self, path: Path) -> str:
        parts = list(path.relative_to(self.src.parent).with_suffix("").parts)
        return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)

    def is_module(self, dotted: str) -> bool:
        base = self.src.parent.joinpath(*dotted.split("."))
        return base.with_suffix(".py").is_file() or (base / "__init__.py").is_file()

    def deepest_module(self, dotted: str) -> str | None:
        parts = dotted.split(".")
        for end in range(len(parts), 0, -1):
            if self.is_module(".".join(parts[:end])):
                return ".".join(parts[:end])
        return None

    def service_of(self, dotted: str) -> str | None:
        parts = dotted.split(".")
        return parts[2] if len(parts) > 2 and parts[:2] == ["src", "services"] and parts[2] in self.services else None

    def owner(self, path: Path) -> str | None:
        """The service a file belongs to (its tests included), or None for core/api/shared code."""
        parts = path.relative_to(self.src).parts
        return parts[1] if len(parts) > 2 and parts[0] == "services" and parts[1] in self.services else None

    def imports(self, path: Path) -> list[Import]:
        tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
        module = self.module_of(path)
        package = module if path.name == "__init__.py" else module.rpartition(".")[0]
        found: list[Import] = []
        bindings: dict[str, str] = {}  # module-level name -> module it refers to

        for node, import_time in _walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    target = self.deepest_module(alias.name)
                    if target:
                        bound = alias.asname or alias.name.split(".")[0]
                        bindings[bound] = alias.name if alias.asname else alias.name.split(".")[0]
                        found.append(Import(node.lineno, target, import_time, bound))
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if node.level:
                    anchor = package.split(".")[: len(package.split(".")) - (node.level - 1)]
                    base = ".".join(anchor + ([base] if base else []))
                for alias in node.names:
                    full = f"{base}.{alias.name}"
                    target = full if self.is_module(full) else self.deepest_module(base)
                    if target:
                        bound = alias.asname or alias.name
                        if target == full:
                            bindings[bound] = full
                        found.append(Import(node.lineno, target, import_time, bound))
        for node, import_time in _walk(tree):
            chain = _attribute_chain(node)
            if chain and chain[0] in bindings:
                target = self.deepest_module(".".join([bindings[chain[0]], *chain[1:]]))
                if target and target != bindings[chain[0]]:
                    found.append(Import(node.lineno, target, import_time))
        return found


def _walk(tree: ast.AST) -> Iterator[tuple[ast.AST, bool]]:
    """Yield every node with whether it runs at import time (not in a function or TYPE_CHECKING block)."""
    stack: list[tuple[ast.AST, bool]] = [(tree, True)]
    while stack:
        node, import_time = stack.pop()
        yield node, import_time
        for child in ast.iter_child_nodes(node):
            runs_now = import_time and not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))
            if isinstance(node, ast.If) and child in node.body and _is_type_checking(node.test):
                runs_now = False
            stack.append((child, runs_now))


def _is_type_checking(test: ast.expr) -> bool:
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
        isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
    )


def _attribute_chain(node: ast.AST) -> list[str] | None:
    """``a.b.c`` -> ["a", "b", "c"]; None for anything that is not a plain dotted name."""
    names: list[str] = []
    while isinstance(node, ast.Attribute):
        names.append(node.attr)
        node = node.value
    return [node.id, *reversed(names)] if isinstance(node, ast.Name) and names else None


def _allowed(rel: str, target: str) -> bool:
    """Whether *rel* importing *target* is one of the ALLOWED_DEEP_IMPORTS (``*`` = one dotted segment)."""
    for importer, pattern, _reason in ALLOWED_DEEP_IMPORTS:
        if rel != importer:
            continue
        head, star, tail = pattern.partition("*")
        if not star and target == pattern:
            return True
        if star and target.startswith(head) and target.endswith(tail):
            segment = target[len(head):len(target) - len(tail)]
            if segment and "." not in segment:
                return True
    return False


def _provider_of(parts: tuple[str, ...]) -> tuple[str, str] | None:
    """(service, provider entry) for ("services", svc, "providers", entry, ...) paths/dotted names.

    ``providers/__init__.py`` is the service's provider loader (it may import every provider), not an entry.
    """
    if len(parts) > 3 and parts[0] == "services" and parts[2] == "providers" and parts[3] != "__init__.py":
        return parts[1], Path(parts[3]).stem
    return None


# --- rules -------------------------------------------------------------------------------------------------


def facade_bypasses(cb: Codebase) -> list[str]:
    """Rule 1: another service's submodules (including its providers) are reached only through its facade."""
    out = []
    for path in cb.files():
        rel, importer = cb.rel(path), cb.owner(path)
        for imp in cb.imports(path):
            svc = cb.service_of(imp.target)
            if svc is None or svc == importer or imp.target == f"src.services.{svc}" or _allowed(rel, imp.target):
                continue
            what = "provider internals" if ".providers" in imp.target else "an internal module"
            out.append(f"{rel}:{imp.line} reaches {what} of '{svc}' ({imp.target}); use `src.services.{svc}`")
    return sorted(set(out))


def sibling_provider_imports(cb: Codebase) -> list[str]:
    """Rule 2: providers don't depend on sibling providers; shared ``_`` helpers don't depend on providers."""
    out = []
    for path in cb.files():
        here = _provider_of(path.relative_to(cb.src).parts)
        if here is None:
            continue
        svc, entry = here
        for imp in cb.imports(path):
            parts = tuple(imp.target.split(".")[1:])
            if parts == ("services", svc, "providers") and not entry.startswith("_"):
                out.append(f"{cb.rel(path)}:{imp.line} imports the providers package, which loads every sibling")
            there = _provider_of(parts)
            if there is None or there[0] != svc or there[1] == entry or there[1].startswith("_"):
                continue
            who = "shared helper" if entry.startswith("_") else "provider"
            out.append(f"{cb.rel(path)}:{imp.line} {who} '{entry}' imports provider '{there[1]}' ({imp.target})")
    return sorted(set(out))


def provider_layout_problems(cb: Codebase) -> list[str]:
    """Rule 3: each concrete provider is a package, and registers itself from inside that package."""
    out = []
    for svc in cb.services:
        providers = cb.src / "services" / svc / "providers"
        if not providers.is_dir():
            continue
        if not (providers / "__init__.py").is_file():
            out.append(f"services/{svc}/providers/ has no __init__.py")
        for entry in sorted(providers.iterdir()):
            if entry.name in {"__init__.py", "__pycache__"} or entry.name.startswith((".", "_")):
                continue
            if entry.is_file() and entry.suffix == ".py":
                out.append(f"services/{svc}/providers/{entry.name} is a module; make it a package {entry.stem}/")
            elif entry.is_dir() and not (entry / "__init__.py").is_file():
                out.append(f"services/{svc}/providers/{entry.name}/ has no __init__.py")
    for path in cb.files():
        rel_parts = path.relative_to(cb.src).parts
        here = _provider_of(rel_parts)
        if "__tests__" in rel_parts or (here and not here[1].startswith("_") and len(rel_parts) > 4):
            continue  # tests register fakes; a provider package registers itself
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            provider_id = _registered_provider_id(node)
            if provider_id is not None:
                where = f"{cb.rel(path)}:{node.lineno}"
                out.append(f"{where} registers provider {provider_id!r} outside a provider package")
    return out


def _registered_provider_id(node: ast.AST) -> str | None:
    """The id in a ``<Something>Factory.register("<id>", ...)`` call, else None."""
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "register"):
        return None
    factory = node.func.value
    if not (isinstance(factory, ast.Name) and factory.id.endswith("Factory") and node.args):
        return None
    first = node.args[0]
    return first.value if isinstance(first, ast.Constant) and isinstance(first.value, str) else None


def facade_problems(cb: Codebase) -> list[str]:
    """Rule 4: facades declare __all__, export only names they bind, and never a provider implementation."""
    out = []
    for svc in cb.services:
        init = cb.src / "services" / svc / "__init__.py"
        tree = ast.parse(init.read_text(encoding="utf-8"))
        exported = next((
            [e.value for e in node.value.elts if isinstance(e, ast.Constant)]
            for node in tree.body
            if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets)
            and isinstance(node.value, (ast.List, ast.Tuple))
        ), None)
        if not exported:
            out.append(f"services/{svc}/__init__.py declares no __all__ (its public interface)")
            continue
        bound = {
            t.id for node in tree.body if isinstance(node, ast.Assign) for t in node.targets if isinstance(t, ast.Name)
        }
        bound |= {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
        from_providers = set()
        for imp in cb.imports(init):
            if imp.bound_name:
                bound.add(imp.bound_name)
                if imp.target.startswith(f"src.services.{svc}.providers."):
                    from_providers.add(imp.bound_name)
        facade = f"services/{svc}/__init__.py"
        out += [f"{facade} exports {n!r}, which it never defines" for n in exported if n not in bound]
        out += [f"{facade} exports provider implementation {n!r}" for n in exported if n in from_providers]
    return out


def concrete_provider_imports(cb: Codebase) -> list[str]:
    """Rule 6: a service's own code reaches its providers through its factory, not by importing one.

    Exempt: files under ``providers/`` (rule 2 governs them, and the loader ``providers/__init__.py`` imports
    every provider) and tests. Importing the loader package itself is how a facade registers the providers.
    """
    out = []
    for path in cb.files():
        rel_parts, svc = path.relative_to(cb.src).parts, cb.owner(path)
        if svc is None or "__tests__" in rel_parts or rel_parts[2] == "providers":
            continue
        for imp in cb.imports(path):
            there = _provider_of(tuple(imp.target.split(".")[1:]))
            if there is None or there[0] != svc or there[1].startswith("_") or _allowed(cb.rel(path), imp.target):
                continue
            out.append(f"{cb.rel(path)}:{imp.line} imports concrete provider '{there[1]}' ({imp.target}); "
                       "select it through the service's provider factory")
    return sorted(set(out))


def service_dependency_graph(cb: Codebase) -> dict[str, set[str]]:
    """Service -> services it imports at import time (runtime code only, tests excluded)."""
    graph: dict[str, set[str]] = {svc: set() for svc in cb.services}
    for path in cb.files():
        importer = cb.owner(path)
        if importer is None or "__tests__" in path.parts:
            continue
        for imp in cb.imports(path):
            target = cb.service_of(imp.target)
            if imp.import_time and target and target != importer:
                graph[importer].add(target)
    return graph


def dependency_cycles(graph: dict[str, set[str]]) -> list[list[str]]:
    """Rule 5: strongly connected components with more than one service (Tarjan)."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    cycles: list[list[str]] = []

    def visit(node: str) -> None:
        index[node] = low[node] = len(index)
        stack.append(node)
        for nxt in sorted(graph.get(node, ())):
            if nxt not in index:
                visit(nxt)
                low[node] = min(low[node], low[nxt])
            elif nxt in stack:
                low[node] = min(low[node], index[nxt])
        if low[node] == index[node]:
            component = []
            while True:
                member = stack.pop()
                component.append(member)
                if member == node:
                    break
            if len(component) > 1:
                cycles.append(sorted(component))

    for node in sorted(graph):
        if node not in index:
            visit(node)
    return cycles


# --- the real codebase ---------------------------------------------------------------------------------------

CODEBASE = Codebase(SRC)


def _report(problems: list[str]) -> str:
    return "\n".join(["", *problems])


def test_cross_service_imports_use_the_public_facade() -> None:
    assert not (problems := facade_bypasses(CODEBASE)), _report(problems)


def test_providers_do_not_import_sibling_providers() -> None:
    assert not (problems := sibling_provider_imports(CODEBASE)), _report(problems)


def test_every_concrete_provider_is_its_own_package() -> None:
    assert not (problems := provider_layout_problems(CODEBASE)), _report(problems)


def test_service_facades_declare_a_public_interface() -> None:
    assert not (problems := facade_problems(CODEBASE)), _report(problems)


def test_services_select_providers_through_their_factory() -> None:
    assert not (problems := concrete_provider_imports(CODEBASE)), _report(problems)


def test_services_have_no_import_time_dependency_cycles() -> None:
    graph = service_dependency_graph(CODEBASE)
    assert not (cycles := dependency_cycles(graph)), f"service import cycles: {cycles}\ngraph: {graph}"


def test_the_checks_see_the_whole_codebase() -> None:
    # Guards against the rules passing vacuously (wrong root, renamed folders).
    assert {"content", "text_to_speech", "evaluation", "logging"} <= set(CODEBASE.services)
    graph = service_dependency_graph(CODEBASE)
    assert "text_to_speech" in graph["evaluation"] and "logging" in graph["text_to_speech"]
    providers = {_provider_of(path.relative_to(SRC).parts) for path in CODEBASE.files()}
    assert {("evaluation", "openai_stt"), ("text_to_speech", "openai_tts")} <= providers


# --- the checks themselves, on a small synthetic codebase ----------------------------------------------------


def _tree(root: Path, files: dict[str, str]) -> Codebase:
    for rel, body in files.items():
        path = root / "src" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
    return Codebase(root / "src")


@pytest.fixture(name="bad")
def fixture_bad(tmp_path: Path) -> Codebase:
    return _tree(tmp_path, {
        "__init__.py": "",
        "services/__init__.py": "",
        "services/alpha/__init__.py": (
            "from src.services.alpha.service import AlphaService\n"
            "from src.services.alpha.providers.one import OneProvider\n"
            "__all__ = ['AlphaService', 'OneProvider', 'Missing']\n"
        ),
        "services/alpha/service.py": "from src.services import beta\nclass AlphaService: ...\n",
        "services/alpha/engine.py": (
            "import src.services.alpha.providers\n"
            "from src.services.alpha.providers import one\n"
            "from src.services.alpha.providers._shared import OneProvider\n"
        ),
        "services/alpha/providers/__init__.py": "",
        "services/alpha/providers/_shared.py": "from src.services.alpha.providers.one.provider import OneProvider\n",
        "services/alpha/providers/one/__init__.py": "from .provider import OneProvider\n",
        "services/alpha/providers/one/provider.py": (
            "from ..two import helper\n"
            "from .._shared import OneProvider as _ok\n"
            "class OneProvider: ...\n"
        ),
        "services/alpha/providers/two.py": "def helper(): ...\nAlphaFactory.register('two', object)\n",
        "services/beta/__init__.py": "from src.services.beta.service import BetaService\n__all__ = ['BetaService']\n",
        "services/beta/service.py": (
            "from src.services.alpha.service import AlphaService\n"
            "import src.services.alpha.providers.one as one\n"
            "from src.services import alpha\n"
            "class BetaService:\n"
            "    def run(self):\n"
            "        from src.services.alpha import service\n"
            "        return alpha.service.AlphaService\n"
        ),
    })


def test_checker_flags_facade_bypasses_including_aliases_and_attribute_access(bad: Codebase) -> None:
    problems = "\n".join(facade_bypasses(bad))
    assert "services/beta/service.py:1 reaches an internal module of 'alpha' (src.services.alpha.service)" in problems
    assert "services/beta/service.py:2 reaches provider internals of 'alpha'" in problems
    assert "services/beta/service.py:6 reaches an internal module" in problems  # from pkg import submodule
    assert "services/beta/service.py:7 reaches an internal module" in problems  # alpha.service.X
    assert "services/alpha/" not in problems  # a service may use its own internals


def test_checker_flags_sibling_provider_imports(bad: Codebase) -> None:
    problems = sibling_provider_imports(bad)
    assert any("provider 'one' imports provider 'two'" in p for p in problems)  # relative import resolved
    assert any("shared helper '_shared' imports provider 'one'" in p for p in problems)
    assert not any("imports provider '_shared'" in p for p in problems)  # using a shared helper is fine


def test_checker_flags_providers_that_are_not_packages(bad: Codebase) -> None:
    problems = provider_layout_problems(bad)
    assert "services/alpha/providers/two.py is a module; make it a package two/" in problems
    assert any("registers provider 'two' outside a provider package" in p for p in problems)


def test_checker_flags_facade_exports(bad: Codebase) -> None:
    problems = facade_problems(bad)
    assert "services/alpha/__init__.py exports provider implementation 'OneProvider'" in problems
    assert "services/alpha/__init__.py exports 'Missing', which it never defines" in problems


def test_checker_flags_service_code_importing_a_concrete_provider(bad: Codebase) -> None:
    problems = concrete_provider_imports(bad)
    assert problems == [
        "services/alpha/__init__.py:2 imports concrete provider 'one' (src.services.alpha.providers.one); "
        "select it through the service's provider factory",
        "services/alpha/engine.py:2 imports concrete provider 'one' (src.services.alpha.providers.one); "
        "select it through the service's provider factory",
    ]  # the provider loader (engine.py:1), shared helpers (engine.py:3) and providers themselves are fine


def test_checker_finds_import_time_cycles_but_ignores_function_level_imports(bad: Codebase) -> None:
    graph = service_dependency_graph(bad)
    assert dependency_cycles(graph) == [["alpha", "beta"]]
    (bad.src / "services/alpha/service.py").write_text("def f():\n    from src.services import beta\n")
    assert not dependency_cycles(service_dependency_graph(bad))
