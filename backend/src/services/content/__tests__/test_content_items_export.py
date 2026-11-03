"""HTTP tests for GET /api/v1/content/categories/{category_id}/items/export (CSV export).

The response contract under test (also documented in docs_agents/backend/services/content/README.md):

- 200, ``Content-Type: text/csv; charset=utf-8``, ``Content-Disposition: attachment; filename="<id>_items.csv"``
  with every character outside ``[A-Za-z0-9._-]`` replaced by ``_``.
- The body is UTF-8 and starts with a BOM (U+FEFF) for Excel; parse it with the ``utf-8-sig`` codec.
- Columns ``id,name_en,name_es,name_fr,name_bg,images_count``; rows sorted by item id; CRLF line ends;
  minimal quoting; the header row is present even for an empty category.
- Names are the stored per-language values (no English fallback): a missing language is an empty cell.
- Text cells starting with ``=``, ``+``, ``-``, ``@``, tab, CR or LF are prefixed with ``'``.
- Errors: 401 unauthenticated, 404 missing category, 400 invalid id, 500 with a fixed message for any
  other provider error (never the provider message, which may contain filesystem paths).
"""

# pylint: disable=missing-function-docstring

from __future__ import annotations

import csv
import io
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from src.services.content.__tests__.content_fs_helpers import (
    make_fs_provider,
    patch_fs_settings,
    write_category_registry,
    write_item_registry,
)
from src.services.content.__tests__.content_http_helpers import StubContentProvider, make_app
from src.services.content.models import (
    CategoryDetails,
    CategoryNotFoundError,
    ContentOperationError,
    InvalidCategoryNameError,
    ItemDetail,
)

_EXPORT_URL = "/api/v1/content/categories/C_Foods/items/export"
_HEADER = ["id", "name_en", "name_es", "name_fr", "name_bg", "images_count"]


async def _export(provider: StubContentProvider, tmp_path: Path, url: str = _EXPORT_URL):
    app = make_app(provider, tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        return await c.get(url)


def _rows(response) -> list[list[str]]:
    text = response.content.decode("utf-8-sig")
    return list(csv.reader(io.StringIO(text, newline="")))


def _details(*items: ItemDetail) -> CategoryDetails:
    return CategoryDetails(id="C_Foods", name={"en": "Foods"}, items=list(items))


# ---------------------------------------------------------------------------
# Auth and errors
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_export_requires_auth(client_no_auth: AsyncClient) -> None:
    response = await client_no_auth.get(_EXPORT_URL)
    assert response.status_code == 401
    # The JSON items route rejects the same way.
    assert (await client_no_auth.get("/api/v1/content/categories/C_Foods/items")).status_code == 401


@pytest.mark.asyncio
async def test_export_missing_category_returns_404(tmp_resolved: Path) -> None:
    provider = StubContentProvider(tmp_resolved).raises(
        "category_details", CategoryNotFoundError("Category id 'C_Nope' is not found in categories_ids.csv."),
    )
    response = await _export(provider, tmp_resolved, "/api/v1/content/categories/C_Nope/items/export")
    assert response.status_code == 404
    assert "C_Nope" in response.json()["detail"]


@pytest.mark.asyncio
async def test_export_invalid_category_returns_400(tmp_resolved: Path) -> None:
    provider = StubContentProvider(tmp_resolved).raises("category_details", InvalidCategoryNameError("../etc"))
    response = await _export(provider, tmp_resolved)
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_export_provider_error_returns_500_without_paths(tmp_resolved: Path) -> None:
    secret_path = f"{tmp_resolved}/content/foods/items_en.csv"
    provider = StubContentProvider(tmp_resolved).raises(
        "category_details", ContentOperationError(f"Cannot read {secret_path}"),
    )
    response = await _export(provider, tmp_resolved)
    assert response.status_code == 500
    assert response.json() == {"detail": "Failed to export the category items."}
    assert str(tmp_resolved) not in response.text


@pytest.mark.asyncio
async def test_export_unexpected_exception_returns_500_without_paths(tmp_resolved: Path) -> None:
    provider = StubContentProvider(tmp_resolved).raises(
        "category_details", OSError(f"[Errno 13] Permission denied: '{tmp_resolved}/content'"),
    )
    response = await _export(provider, tmp_resolved)
    assert response.status_code == 500
    assert str(tmp_resolved) not in response.text


# ---------------------------------------------------------------------------
# Response format
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_export_empty_category_has_header_only(tmp_resolved: Path) -> None:
    provider = StubContentProvider(tmp_resolved).returns("category_details", _details())
    response = await _export(provider, tmp_resolved)
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/csv; charset=utf-8"
    assert response.headers["content-disposition"] == 'attachment; filename="C_Foods_items.csv"'
    # Documented: UTF-8 BOM first, CRLF line ends.
    assert response.content == b"\xef\xbb\xbfid,name_en,name_es,name_fr,name_bg,images_count\r\n"
    assert _rows(response) == [_HEADER]


@pytest.mark.asyncio
async def test_export_localized_names_sorted_by_id(tmp_resolved: Path) -> None:
    provider = StubContentProvider(tmp_resolved).returns("category_details", _details(
        ItemDetail(id="I_Tea", name={"en": "tea", "es": "té", "fr": "thé", "bg": "чай"}, images_count=1),
        ItemDetail(id="I_Coffee", name={"en": "coffee", "es": "café", "fr": "café", "bg": "кафе"},
                   images_count=3),
        ItemDetail(id="I_Milk", name={"en": "milk"}, images_count=0),
    ))
    response = await _export(provider, tmp_resolved)
    assert response.status_code == 200
    assert _rows(response) == [
        _HEADER,
        ["I_Coffee", "coffee", "café", "café", "кафе", "3"],
        ["I_Milk", "milk", "", "", "", "0"],  # missing languages stay empty (no English fallback)
        ["I_Tea", "tea", "té", "thé", "чай", "1"],
    ]


@pytest.mark.asyncio
async def test_export_quotes_delimiters_quotes_and_newlines(tmp_resolved: Path) -> None:
    provider = StubContentProvider(tmp_resolved).returns("category_details", _details(
        ItemDetail(id="I_A", name={"en": 'salt, "sea"', "es": "línea\nnueva", "fr": "a;b", "bg": " x "}),
    ))
    response = await _export(provider, tmp_resolved)
    assert '"salt, ""sea"""' in response.content.decode("utf-8-sig")
    assert _rows(response)[1] == ["I_A", 'salt, "sea"', "línea\nnueva", "a;b", " x ", "0"]


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["=1+1", "+SUM(A1)", "-2+3", "@cmd", "\tx", "\rx", "\nx"])
async def test_export_neutralizes_formula_triggers(tmp_resolved: Path, value: str) -> None:
    provider = StubContentProvider(tmp_resolved).returns("category_details", _details(
        ItemDetail(id=value, name={"en": value, "es": value, "fr": "safe", "bg": value}, images_count=2),
    ))
    response = await _export(provider, tmp_resolved)
    row = _rows(response)[1]
    assert row == ["'" + value, "'" + value, "'" + value, "safe", "'" + value, "2"]


@pytest.mark.asyncio
async def test_export_filename_is_sanitized(tmp_resolved: Path) -> None:
    provider = StubContentProvider(tmp_resolved).returns("category_details", _details())
    response = await _export(provider, tmp_resolved, '/api/v1/content/categories/a"b%0D%0Ac;d/items/export')
    assert response.status_code == 200
    assert response.headers["content-disposition"] == 'attachment; filename="a_b_c_d_items.csv"'


@pytest.mark.asyncio
async def test_export_route_is_not_shadowed_by_item_route(client: AsyncClient) -> None:
    # "export" must never be resolved as an item id by GET .../items/{item_id}.
    response = await client.get(_EXPORT_URL)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")


@pytest.mark.asyncio
async def test_json_items_route_unchanged(client: AsyncClient) -> None:
    response = await client.get("/api/v1/content/categories/C_Foods/items")
    assert response.status_code == 200
    assert response.json() == {
        "items": [{"id": "I_C_Foods", "name": {"en": "apple"}, "thumbnail_uri": None, "images_count": 0}],
    }


# ---------------------------------------------------------------------------
# Real file_system provider over temporary content
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_export_from_filesystem_content(tmp_resolved: Path) -> None:
    content_root = tmp_resolved / "content"
    write_category_registry(content_root, [("C_Foods", "foods")])
    category_path = content_root / "foods"
    write_item_registry(category_path, [("I_Water", "water"), ("I_Apple", "apple")])
    (category_path / "items_bg.csv").write_text("вода\nябълка\n", encoding="utf-8")
    (category_path / "items_es.csv").write_text("=agua\nmanzana\n", encoding="utf-8")

    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = content_root
        mock_settings.SUPPORTED_LANGUAGES = ["en", "es", "fr", "bg"]
        app = make_app(provider, tmp_resolved)  # type: ignore[arg-type]
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            response = await c.get(_EXPORT_URL)

    assert response.status_code == 200, response.text
    rows = _rows(response)
    assert rows[0] == _HEADER
    assert [row[0] for row in rows[1:]] == ["I_Apple", "I_Water"]
    assert rows[1][1:5] == ["apple", "manzana", "", "ябълка"]
    assert rows[2][1:5] == ["water", "'=agua", "", "вода"]
