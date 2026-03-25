"""List commands: generators, translators, optimizers, evaluators."""

import typer

from kiddo_cli.api.client import get, internal_get
from kiddo_cli.auth_flow import run_with_auth
from kiddo_cli.output import print_json


def register(app: typer.Typer) -> None:
    """Register list commands on the given Typer app."""

    @app.command("users")
    def list_users(
            url: str = typer.Option(None, "--url"),
            token: str | None = typer.Option(None, "--token"),
    ):
        """Show configured users with activation states."""

        def _run(t: str):
            result = get("/users", token=t)
            print_json(result)

        run_with_auth(url, token, _run)

    @app.command("translators")
    def list_translators(
            url: str = typer.Option(None, "--url"),
            token: str | None = typer.Option(None, "--token"),
    ):
        """Show configured translators with priority and states."""

        def _run(t: str):
            result = internal_get("/translators", token=t)
            print_json(result)

        run_with_auth(url, token, _run)

    @app.command("generators")
    def list_generators(
            url: str = typer.Option(None, "--url"),
            token: str | None = typer.Option(None, "--token"),
    ):
        """Show configured generators with activation states."""

        def _run(t: str):
            result = internal_get("/generators", token=t)
            print_json(result)

        run_with_auth(url, token, _run)

    @app.command("optimizers")
    def list_optimizers(
            url: str = typer.Option(None, "--url"),
            token: str | None = typer.Option(None, "--token"),
    ):
        """Show configured optimizers with activation states."""

        def _run(t: str):
            result = internal_get("/optimizers", token=t)
            print_json(result)

        run_with_auth(url, token, _run)

    @app.command("services")
    def list_services(
            enabled: bool | None = typer.Option(None, "--enabled/--disabled", help="Filter by enabled state"),
            name: str | None = typer.Option(None, "--name", help="Filter by name pattern"),
            category: str | None = typer.Option(None, "--category", help="Filter by category"),
            data: str | None = typer.Option(None, "--json", help="JSON input string or @file.json for parameters"),
            url: str = typer.Option(None, "--url", help="Backend URL"),
            token: str | None = typer.Option(None, "--token", help="Auth token"),
    ):
        """Get translation services by criteria."""

        def _run(t: str):
            import json
            nonlocal enabled, name, category

            # --- Parse JSON if provided ---
            if data:
                if data.startswith("@"):
                    with open(data[1:], "r", encoding="utf-8") as f:
                        payload = json.load(f)
                else:
                    payload = json.loads(data)

                # Override options with values from JSON
                if "enabled" in payload:
                    enabled = payload["enabled"]
                if "name" in payload:
                    name = payload["name"]
                if "category" in payload:
                    category = payload["category"]

            services = internal_get("/translators", token=t)

            filtered = []
            for s in services:
                # Filter by enabled
                if enabled is not None and s.get("enabled") != enabled:
                    continue

                # Filter by name pattern
                if name:
                    if name.lower() not in s.get("name", "").lower():
                        continue

                # Filter by category
                if category:
                    if category.lower() != s.get("category", "").lower():
                        continue

                filtered.append(s)

            if not name:
                print_json(filtered)
                return

            # Highlight name if name filter is used
            highlighted_output = []
            for s in filtered:
                s_copy = dict(s)
                original_name = s_copy.get("name", "")
                if name.lower() in original_name.lower():
                    # Find the start index for case-insensitive match
                    start = original_name.lower().find(name.lower())
                    end = start + len(name)
                    # Use Typer's styling for highlighting
                    matched_part = typer.style(original_name[start:end], fg=typer.colors.CYAN, bold=True)
                    new_name = original_name[:start] + matched_part + original_name[end:]
                    s_copy["name"] = new_name
                highlighted_output.append(s_copy)

            print_json(highlighted_output)

        run_with_auth(url, token, _run)
