"""Content commands (backend routes_content): list-categories, category-details, create-object."""
from pathlib import Path

import requests
import typer

from kiddo_cli.api.client import delete, get, patch, post
from kiddo_cli.auth_flow import get_token_for_run, run_with_auth
from kiddo_cli.output import print_error, print_json, print_request_error


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
            old_name: str = typer.Argument(..., help="Current category name"),
            to_name: str = typer.Option(..., "--to", help="New category name"),
            url: str = typer.Option(None, "--url", help="Backend base URL"),
            token: str | None = typer.Option(None, "--token", help="Bearer token (else auth via CLI socket)"),
    ):
        """Rename a category (folder + regenerate voices)."""
        def run(t: str):
            result = patch(f"/content/categories/{old_name}", json={"name": to_name}, token=t, timeout=120)
            print_json(result)

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

    @app.command("create-object")
    def create_object(
            image_path: Path = typer.Option(..., "--image", help="Path to image file", path_type=Path),
            category: str = typer.Option(..., "--category", help="Category name"),
            object_word: str = typer.Option(..., "--object", help="English word for the object"),
            url: str = typer.Option(None, "--url", help="Backend base URL"),
            token: str | None = typer.Option(None, "--token", help="Bearer token (else auth via CLI socket)"),
    ):
        """Create a new content object (calls backend API)."""
        if not image_path.exists() or not image_path.is_file():
            print_error("Image file not found", str(image_path))
            raise typer.Exit(1)

        def run(t: str):
            try:
                with open(image_path, "rb") as f:
                    files = {"image": (image_path.name, f, "application/octet-stream")}
                    data = {"category": category, "object": object_word}
                    result = post("/content/objects", data=data, files=files, token=t, timeout=120)
                    print_json(result)
            except requests.RequestException as e:
                print_request_error(e)
                raise typer.Exit(1)

        run_with_auth(url, token, run)
