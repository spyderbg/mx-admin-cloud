"""Tests for scripts/check_docs_agents.py — the docs_agents/ validator."""

# pylint: disable=missing-function-docstring

from __future__ import annotations

from pathlib import Path

import pytest

import check_docs_agents as checker

ITEM_REF = "#/components/schemas/Item"


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _valid_spec() -> dict:
    return {
        "openapi": "3.1.0",
        "info": {"title": "t", "version": "1"},
        "paths": {
            "/items/{item_id}": {
                "get": {
                    "operationId": "get_item",
                    "parameters": [{"name": "item_id", "in": "path", "required": True}],
                    "responses": {"200": {"content": {"application/json": {"schema": {"$ref": ITEM_REF}}}}},
                    "security": [{"Bearer": []}],
                },
            },
        },
        "components": {"schemas": {"Item": {"type": "object"}}, "securitySchemes": {"Bearer": {"type": "http"}}},
    }


@pytest.mark.parametrize("heading,slug", [
    ("6. Contract and code-generation flow", "6-contract-and-code-generation-flow"),
    ("10. Decision D-3 — single machine", "10-decision-d-3--single-machine"),
    ("`ServiceProvider` contract", "serviceprovider-contract"),
    ("See [the docs](x.md) now", "see-the-docs-now"),
])
def test_github_slug(heading: str, slug: str) -> None:
    assert checker.github_slug(heading) == slug


def test_heading_anchors_deduplicate_and_skip_code_fences() -> None:
    text = "# Testing\n## Testing\n```\n# not a heading\n```\n"
    assert checker.heading_anchors(text) == {"testing", "testing-1"}


def test_check_links_reports_missing_files_and_anchors(tmp_path: Path) -> None:
    docs = tmp_path / "docs_agents"
    _write(docs / "target.md", "# Real Heading\n")
    _write(docs / "index.md", "\n".join([
        "[ok](target.md#real-heading)",
        "[ok dir](.)",
        "[external](https://example.com/x)",
        "[same page](#index)",
        "[gone](missing.md)",
        "[bad anchor](target.md#nope)",
        "```",
        "[in code](ignored.md)",
        "```",
        "# Index",
    ]))
    problems = checker.check_links(docs)
    assert len(problems) == 2
    assert any("missing missing.md" in p for p in problems)
    assert any("no heading for #nope" in p for p in problems)


def test_validate_openapi_accepts_a_valid_document() -> None:
    assert not checker.validate_openapi(_valid_spec())


def test_validate_openapi_accepts_empty_paths() -> None:
    assert not checker.validate_openapi({"openapi": "3.1.0", "info": {"title": "t", "version": "1"}, "paths": {}})


def test_validate_openapi_reports_each_structural_problem() -> None:
    spec = _valid_spec()
    spec["openapi"] = "2.0"
    operation = spec["paths"]["/items/{item_id}"]["get"]
    operation["parameters"] = []
    operation["security"] = [{"Missing": []}]
    operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"] = "#/components/schemas/Nope"
    spec["paths"]["/other"] = {"post": {"operationId": "get_item", "responses": {}}}
    problems = "\n".join(checker.validate_openapi(spec))
    assert "openapi must be a 3.x.y" in problems
    assert "path parameter(s) ['item_id'] not declared" in problems
    assert "unknown security scheme 'Missing'" in problems
    assert "unresolved $ref '#/components/schemas/Nope'" in problems
    assert "POST /other: responses must be a non-empty mapping" in problems
    assert "duplicate operationId 'get_item'" in problems


def test_provider_inventory_requires_classes_and_config_files(tmp_path: Path) -> None:
    services = tmp_path / "services"
    _write(services / "tts" / "__init__.py", 'X = Criteria(service_name="tts", config_subdir="text_to_speech")\n')
    _write(services / "tts" / "providers" / "fast.py", "\n".join([
        "class FastTTS(Base):",
        "    @classmethod",
        "    def register(cls):",
        '        Factory.register("fast", cls)',
    ]))
    config = tmp_path / "config"
    _write(config / "text_to_speech" / "fast.json", "{}")
    _write(config / "text_to_speech" / "fast_slow.json", "{}")
    docs = tmp_path / "docs_agents"
    readme = _write(docs / "backend" / "services" / "tts" / "README.md", "`FastTTS` configured by `fast.json`\n")

    assert checker.registered_providers(services / "tts") == [("fast", "FastTTS")]
    assert checker.config_subdir(services / "tts") == "text_to_speech"
    assert checker.check_provider_inventory(docs, services, config) == [
        "tts: README does not name configuration file text_to_speech/fast_slow.json",
    ]
    readme.write_text("`FastTTS`: `fast.json`, `fast_slow.json`\n", encoding="utf-8")
    assert not checker.check_provider_inventory(docs, services, config)


def test_service_docs_require_readme_and_openapi(tmp_path: Path) -> None:
    services = tmp_path / "services"
    _write(services / "content" / "__init__.py", "")
    docs = tmp_path / "docs_agents"
    _write(docs / "backend" / "services" / "content" / "README.md", "x")
    assert checker.check_service_docs(docs, services) == [
        "content: missing docs_agents/backend/services/content/openapi.yaml",
    ]


def test_readme_index_must_link_every_existing_readme(tmp_path: Path) -> None:
    docs = tmp_path / "docs_agents"
    _write(tmp_path / "backend" / "README.md", "x")
    _write(tmp_path / "cli" / "README.md", "x")
    _write(docs / "README.md", "[backend](../backend/README.md)\n")
    assert checker.check_readme_index(docs, ["backend/README.md", "cli/README.md"]) == [
        "docs_agents/README.md does not link pre-existing cli/README.md",
    ]
