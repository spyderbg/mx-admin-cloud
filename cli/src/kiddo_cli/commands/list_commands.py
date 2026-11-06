"""List commands: generators, translators, optimizers, evaluators."""

import json
import re
import typer
from rich import print  # pylint: disable=redefined-builtin
from rich.markup import escape

from kiddo_cli.api.client import get, internal_get, user_mgmt_get
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
            result = user_mgmt_get("/users", token=t)
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

    @app.command("loggers")
    def list_loggers(
            include_all: bool = typer.Option(False, "--all", help="Include all loggers, not just active ones"),
            url: str = typer.Option(None, "--url", help="Backend URL"),
            token: str | None = typer.Option(None, "--token", help="Auth token"),
    ):
        """List registered stdlib loggers. By default, shows only active loggers (those with handlers)."""

        def _run(t: str):
            params = {"all_loggers": "true"} if include_all else None
            result = internal_get("/loggers", token=t, params=params)
            if not isinstance(result, list):
                print_json(result)
                return

            label = "All loggers:" if include_all else "Active loggers:"
            print(f"[bold green]{label} ({len(result)})[/bold green]")
            for p in result:
                name = escape(str(p.get("name", "<unnamed>")))
                provider = str(p.get("provider", "—"))
                enabled = bool(p.get("enabled", False))
                priority = p.get("priority")

                state_color = "green" if enabled else "red"
                state_text = "enabled" if enabled else "disabled"
                priority_suffix = f"  [dim](priority: {priority})[/dim]" if priority is not None else ""
                print(
                    f"- [bold]{name}[/bold]  "
                    f"[{state_color}]{state_text}[/{state_color}]  "
                    f"[dim]provider:[/dim] [cyan]{escape(provider)}[/cyan]"
                    f"{priority_suffix}"
                )

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
        """List internal services by criteria."""

        def _run(t: str):
            nonlocal enabled, name, category

            service_to_category = {
                "logging": "loggers",
                "text_translation": "translators",
                "text_to_speech": "generators",
                "text_to_image": "image_generators",
                "voice_optimization": "optimizers",
                "evaluation": "evaluators",
            }

            valid_categories = set(service_to_category.values())
            category_aliases = {
                "logging": "loggers",
                "translator": "translators",
                "generator": "generators",
                "image_generator": "image_generators",
                "images": "image_generators",
                "optimizer": "optimizers",
                "evaluator": "evaluators",
            }

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

            normalized_category = None
            if category:
                raw_category = category.lower().strip()
                normalized_category = category_aliases.get(raw_category, raw_category)
                if normalized_category not in valid_categories:
                    valid_values = ", ".join(sorted(valid_categories))
                    raise typer.BadParameter(
                        f"Invalid category '{category}'. Use one of: {valid_values}"
                    )

            response = internal_get("/services", token=t)
            if not isinstance(response, list):
                print("[red]Unexpected response from backend.[/red]")
                return

            services = [dict(s) for s in response]

            filtered = []
            for s in services:
                # Filter by enabled
                if enabled is not None and s.get("enabled") != enabled:
                    continue

                # Filter by name pattern
                if name:
                    if name.lower() not in s.get("name", "").lower():
                        continue

                # Filter by category (mapped from the "service" field)
                if normalized_category:
                    mapped = service_to_category.get(s.get("service", ""), "")
                    if normalized_category != mapped:
                        continue

                filtered.append(s)

            if not filtered:
                print("[yellow]No services matched the provided filters.[/yellow]")
                return

            print(f"[bold green]Services ({len(filtered)})[/bold green]")
            category_colors = {
                "loggers": "grey50",
                "translators": "magenta",
                "generators": "cyan",
                "image_generators": "bright_cyan",
                "optimizers": "blue",
                "evaluators": "yellow",
            }

            for service in filtered:
                service_name = str(service.get("name", "<unnamed>"))
                category_name = service_to_category.get(service.get("service", ""), "unknown")
                enabled_state = bool(service.get("enabled"))

                escaped_name = escape(service_name)
                if name:
                    escaped_filter = re.escape(name)
                    escaped_name = re.sub(
                        escaped_filter,
                        lambda match: f"[bold cyan]{escape(match.group(0))}[/bold cyan]",
                        escaped_name,
                        flags=re.IGNORECASE,
                    )

                state_color = "green" if enabled_state else "red"
                state_text = "enabled" if enabled_state else "disabled"
                category_color = category_colors.get(category_name, "white")
                priority = service.get("priority")
                suffix = f"  [dim](priority: {priority})[/dim]" if priority is not None else ""

                print(
                    f"- [{category_color}]{category_name}[/{category_color}] :: "
                    f"[{state_color}]{state_text}[/{state_color}] :: "
                    f"{escaped_name}{suffix}"
                )

        run_with_auth(url, token, _run)
