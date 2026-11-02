"""Content commands (backend routes_content): list-categories, category-details, create-item."""
from pathlib import Path

import requests
import typer

from urllib.parse import quote

from kiddo_cli.api.client import delete, get, get_bytes, patch, post
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

    @app.command("export-items")
    def export_items(
            category_id: str = typer.Argument(..., help="Category id (e.g. C_Foods)"),
            output: Path = typer.Option(..., "--output", help="Path of the CSV file to write", path_type=Path),
            url: str = typer.Option(None, "--url", help="Backend base URL"),
            token: str | None = typer.Option(None, "--token", help="Bearer token (else auth via CLI socket)"),
    ):
        """Download a category's items as CSV, saved exactly as the server sent it."""
        def run(t: str):
            try:
                content = get_bytes(f"/content/categories/{quote(category_id, safe='')}/items/export", token=t)
            except requests.HTTPError as e:
                if e.response is not None and e.response.status_code == 404:
                    print_error("Category not found", category_id)
                    raise typer.Exit(1)
                raise
            # Write only after the full body arrived, so a failed request leaves no partial file.
            try:
                output.write_bytes(content)
            except OSError as e:
                print_error("Cannot write output file", f"{output}: {e}")
                raise typer.Exit(1)
            typer.echo(f"Wrote {len(content)} bytes to {output}")

        run_with_auth(url, token, run)

    @app.command("create-item")
    def create_item(
            category: str = typer.Option(..., "--category", help="Category id (e.g. C_Foods) or name (e.g. foods)"),
            item: str = typer.Option(..., "--item", help="English word for the item"),
            image_path: Path | None = typer.Option(None, "--image", help="Path to image file (optional)", path_type=Path),
            generate_voices: bool = typer.Option(False, "--generate-voices", help="Generate a voice per language"),
            generate_image: bool = typer.Option(
                False, "--generate-image", help="Generate the image from the item name (not with --image)",
            ),
            url: str = typer.Option(None, "--url", help="Backend base URL"),
            token: str | None = typer.Option(None, "--token", help="Bearer token (else auth via CLI socket)"),
    ):
        """Create a new content item (calls backend API)."""
        if image_path is not None and (not image_path.exists() or not image_path.is_file()):
            print_error("Image file not found", str(image_path))
            raise typer.Exit(1)
        if image_path is not None and generate_image:
            # The backend refuses this too; checking here saves the round trip.
            print_error("Choose one image source", "--image and --generate-image cannot be combined")
            raise typer.Exit(1)

        def run(t: str):
            try:
                category_id = _resolve_category_id(category, t)
                if category_id is None:
                    print_error("Category not found", category)
                    raise typer.Exit(1)
                data = {
                    "item": item,
                    "generate_voices": str(generate_voices).lower(),
                    "generate_image": str(generate_image).lower(),
                }
                path = f"/content/categories/{category_id}/items"
                # Image generation can take minutes on a local render backend (the backend allows 600 s).
                timeout = 720 if generate_image else 120
                if image_path is None:
                    result = post(path, data=data, token=t, timeout=timeout)
                else:
                    with open(image_path, "rb") as f:
                        files = {"image": (image_path.name, f, "application/octet-stream")}
                        result = post(path, data=data, files=files, token=t, timeout=timeout)
                print_json(result)
            except requests.RequestException as e:
                print_request_error(e)
                raise typer.Exit(1)

        run_with_auth(url, token, run)
