"""Content commands (backend routes_content): list-categories, category-details, create-item."""
from pathlib import Path

import requests
import typer

from kiddo_cli.api.client import delete, get, patch, post
from kiddo_cli.auth_flow import run_with_auth
from kiddo_cli.output import print_error, print_json, print_request_error


def _resolve_category_id(category: str, token: str) -> str | None:
    """Return the id of the category matching *category* by id or English name, else None."""
    categories = get("/content/categories", token=token).get("categories", [])
    for entry in categories:
        if entry.get("id") == category:
            return entry["id"]
    wanted = category.replace("_", " ").strip().lower()
    for entry in categories:
        english = (entry.get("name") or {}).get("en", "")
        if english.replace("_", " ").strip().lower() == wanted:
            return entry["id"]
    return None


def register(app: typer.Typer) -> None:
    """Register content commands on the given Typer app."""

    @app.command("categories")
    def list_categories(
            url: str = typer.Option(None, "--url", help="Backend base URL"),
            token: str | None = typer.Option(None, "--token", help="Bearer token (else auth via CLI socket)"),
            include_archived: bool = typer.Option(False, "--include-archived", help="Also list archived categories"),
            archived_only: bool = typer.Option(False, "--archived-only", help="List only archived categories"),
    ):
        """List content categories (calls backend API)."""
        def run(t: str):
            params = {}
            if include_archived:
                params["include_archived"] = "true"
            if archived_only:
                params["archived_only"] = "true"
            result = get("/content/categories", token=t, params=params if params else None)
            print_json(result)

        run_with_auth(url, token, run)

    @app.command("create-category")
    def create_category(
            name: str = typer.Option(..., "--name", help="Category name"),
            url: str = typer.Option(None, "--url", help="Backend base URL"),
            token: str | None = typer.Option(None, "--token", help="Bearer token (else auth via CLI socket)"),
    ):
        """Create a new category (folder, voices, icon)."""
        def run(t: str):
            result = post("/content/categories", json={"name": name}, token=t, timeout=120)
            print_json(result)

        run_with_auth(url, token, run)

    @app.command("rename-category")
    def rename_category(
            old_name: str = typer.Argument(..., help="Current category id (e.g. C_Foods) or English name (e.g. foods)"),
            to_name: str = typer.Option(..., "--to", help="New category id"),
            url: str = typer.Option(None, "--url", help="Backend base URL"),
            token: str | None = typer.Option(None, "--token", help="Bearer token (else auth via CLI socket)"),
    ):
        """Change a category's id; its localized names and description stay as they are."""
        def run(t: str):
            try:
                category_id = _resolve_category_id(old_name, t)
                if category_id is None:
                    print_error("Category not found", old_name)
                    raise typer.Exit(1)
                # The endpoint requires the names and description, so resend the current ones.
                details = get(f"/content/categories/{category_id}", token=t)
                body = {
                    "new_category_id": to_name,
                    "category_name": details.get("name") or {},
                    "description": details.get("description") or {},
                }
                result = patch(f"/content/categories/{category_id}", json=body, token=t, timeout=120)
                print_json(result)
            except requests.RequestException as e:
                print_request_error(e)
                raise typer.Exit(1)

        run_with_auth(url, token, run)

    @app.command("restore-category")
    def restore_category(
            name: str = typer.Argument(..., help="Category name or archive folder (e.g. foods_1738456789)"),
            url: str = typer.Option(None, "--url", help="Backend base URL"),
            token: str | None = typer.Option(None, "--token", help="Bearer token (else auth via CLI socket)"),
    ):
        """Restore a category from archive (calls backend API)."""
        def run(t: str):
            result = post("/content/categories/restore", json={"name": name}, token=t)
            print_json(result)

        run_with_auth(url, token, run)

    @app.command("remove-category")
    def remove_category(
            name: str = typer.Argument(..., help="Category name"),
            url: str = typer.Option(None, "--url", help="Backend base URL"),
            token: str | None = typer.Option(None, "--token", help="Bearer token (else auth via CLI socket)"),
    ):
        """Remove a category (archive if ARCHIVE_DIR set, else delete permanently)."""
        def run(t: str):
            result = delete(f"/content/categories/{name}", token=t)
            print_json(result)

        run_with_auth(url, token, run)

    @app.command("category-details")
    def category_details_cmd(
            category_name: str = typer.Argument(..., help="Category name"),
            url: str = typer.Option(None, "--url", help="Backend base URL"),
            token: str | None = typer.Option(None, "--token", help="Bearer token (else auth via CLI socket)"),
    ):
        """Show category details (calls backend API)."""
        def run(t: str):
            result = get(f"/content/categories/{category_name}", token=t)
            print_json(result)

        run_with_auth(url, token, run)

    @app.command("create-item")
    def create_item(
            category: str = typer.Option(..., "--category", help="Category id (e.g. C_Foods) or name (e.g. foods)"),
            item: str = typer.Option(..., "--item", help="English word for the item"),
            image_path: Path | None = typer.Option(None, "--image", help="Path to image file (optional)", path_type=Path),
            url: str = typer.Option(None, "--url", help="Backend base URL"),
            token: str | None = typer.Option(None, "--token", help="Bearer token (else auth via CLI socket)"),
    ):
        """Create a new content item (calls backend API)."""
        if image_path is not None and (not image_path.exists() or not image_path.is_file()):
            print_error("Image file not found", str(image_path))
            raise typer.Exit(1)

        def run(t: str):
            try:
                category_id = _resolve_category_id(category, t)
                if category_id is None:
                    print_error("Category not found", category)
                    raise typer.Exit(1)
                data = {"item": item}
                path = f"/content/categories/{category_id}/items"
                if image_path is None:
                    result = post(path, data=data, token=t, timeout=120)
                else:
                    with open(image_path, "rb") as f:
                        files = {"image": (image_path.name, f, "application/octet-stream")}
                        result = post(path, data=data, files=files, token=t, timeout=120)
                print_json(result)
            except requests.RequestException as e:
                print_request_error(e)
                raise typer.Exit(1)

        run_with_auth(url, token, run)
