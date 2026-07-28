"""Configuration commands: inspect and modify service configuration files."""

import json
from pathlib import Path
from typing import List, Optional

import typer

from kiddo_cli.api.client import internal_delete, internal_get, internal_patch, internal_post
from kiddo_cli.auth_flow import run_with_auth
from kiddo_cli.output import print_json


def _load_json(data: str) -> dict:
    """Parse an inline JSON object string or an @file.json reference."""
    if data.startswith("@"):
        with open(data[1:], "r", encoding="utf-8") as f:
            payload = json.load(f)
    else:
        payload = json.loads(data)
    if not isinstance(payload, dict):
        raise typer.BadParameter("JSON payload must be an object.")
    return payload


def register(app: typer.Typer) -> None:
    """Register configuration commands on the given Typer app."""

    @app.command("services")
    def list_services(
        url: str = typer.Option(None, "--url"),
        token: Optional[str] = typer.Option(None, "--token"),
    ):
        """List services that accept configuration files."""

        def _run(t: str):
            print_json(internal_get("/configurations", token=t))

        run_with_auth(url, token, _run)

    @app.command("list")
    def list_configs(
        service: str = typer.Argument(..., help="Service name (see 'config services')"),
        url: str = typer.Option(None, "--url"),
        token: Optional[str] = typer.Option(None, "--token"),
    ):
        """List configuration files for one service."""

        def _run(t: str):
            print_json(internal_get(f"/configurations/{service}", token=t))

        run_with_auth(url, token, _run)

    @app.command("show")
    def show_config(
        service: str = typer.Argument(..., help="Service name"),
        name: str = typer.Argument(..., help="Configuration name (filename without .json)"),
        url: str = typer.Option(None, "--url"),
        token: Optional[str] = typer.Option(None, "--token"),
    ):
        """Show the full JSON payload of one configuration file."""

        def _run(t: str):
            print_json(internal_get(f"/configurations/{service}/{name}", token=t))

        run_with_auth(url, token, _run)

    @app.command("set")
    def set_config(
        service: str = typer.Argument(..., help="Service name"),
        name: str = typer.Argument(..., help="Configuration name"),
        enabled: Optional[bool] = typer.Option(
            None, "--enabled/--disabled", help="Enable or disable the configuration"
        ),
        priority: Optional[int] = typer.Option(None, "--priority", help="Provider priority"),
        version: Optional[str] = typer.Option(
            None, "--version", help="Configuration version (empty string removes the field)"
        ),
        url: str = typer.Option(None, "--url"),
        token: Optional[str] = typer.Option(None, "--token"),
    ):
        """Update common service_provider fields: enabled, priority, version."""
        fields: dict = {}
        if enabled is not None:
            fields["enabled"] = enabled
        if priority is not None:
            fields["priority"] = priority
        if version is not None:
            fields["version"] = version
        if not fields:
            raise typer.BadParameter(
                "Provide at least one of --enabled/--disabled, --priority, --version."
            )

        def _run(t: str):
            body = {"patch": {"service_provider": fields}}
            print_json(internal_patch(f"/configurations/{service}/{name}", token=t, json=body))

        run_with_auth(url, token, _run)

    @app.command("patch")
    def patch_config(
        service: str = typer.Argument(..., help="Service name"),
        name: str = typer.Argument(..., help="Configuration name"),
        data: str = typer.Option(..., "--json", help="Merge-patch JSON string or @file.json"),
        url: str = typer.Option(None, "--url"),
        token: Optional[str] = typer.Option(None, "--token"),
    ):
        """Merge-patch arbitrary fields of one configuration file."""
        patch = _load_json(data)

        def _run(t: str):
            print_json(internal_patch(f"/configurations/{service}/{name}", token=t, json={"patch": patch}))

        run_with_auth(url, token, _run)

    @app.command("create")
    def create_config(
        service: str = typer.Argument(..., help="Service name"),
        name: str = typer.Argument(..., help="Name for the new configuration"),
        data: str = typer.Option(..., "--json", help="Full config JSON string or @file.json"),
        url: str = typer.Option(None, "--url"),
        token: Optional[str] = typer.Option(None, "--token"),
    ):
        """Create a new configuration file for one service."""
        payload = _load_json(data)

        def _run(t: str):
            body = {"config_name": name, "payload": payload}
            print_json(internal_post(f"/configurations/{service}", token=t, json=body))

        run_with_auth(url, token, _run)

    @app.command("delete")
    def delete_config(
        service: str = typer.Argument(..., help="Service name"),
        name: str = typer.Argument(..., help="Configuration name to delete"),
        url: str = typer.Option(None, "--url"),
        token: Optional[str] = typer.Option(None, "--token"),
    ):
        """Delete one configuration file."""

        def _run(t: str):
            print_json(internal_delete(f"/configurations/{service}/{name}", token=t))

        run_with_auth(url, token, _run)

    @app.command("enable")
    def enable_configs(
        service: str = typer.Argument(..., help="Service name"),
        names: List[str] = typer.Argument(..., help="Configuration names to enable"),
        url: str = typer.Option(None, "--url"),
        token: Optional[str] = typer.Option(None, "--token"),
    ):
        """Enable selected configurations for one service."""

        def _run(t: str):
            body = {"config_names": list(names)}
            print_json(internal_post(f"/configurations/{service}/enable-configurations", token=t, json=body))

        run_with_auth(url, token, _run)

    @app.command("disable")
    def disable_configs(
        service: str = typer.Argument(..., help="Service name"),
        names: List[str] = typer.Argument(..., help="Configuration names to disable"),
        url: str = typer.Option(None, "--url"),
        token: Optional[str] = typer.Option(None, "--token"),
    ):
        """Disable selected configurations for one service."""

        def _run(t: str):
            body = {"config_names": list(names)}
            print_json(internal_post(f"/configurations/{service}/disable-configurations", token=t, json=body))

        run_with_auth(url, token, _run)

    @app.command("enable-all")
    def enable_all(
        service: str = typer.Argument(..., help="Service name"),
        url: str = typer.Option(None, "--url"),
        token: Optional[str] = typer.Option(None, "--token"),
    ):
        """Enable every configuration for one service."""

        def _run(t: str):
            print_json(internal_post(f"/configurations/{service}/enable-all-configurations", token=t))

        run_with_auth(url, token, _run)

    @app.command("disable-all")
    def disable_all(
        service: str = typer.Argument(..., help="Service name"),
        url: str = typer.Option(None, "--url"),
        token: Optional[str] = typer.Option(None, "--token"),
    ):
        """Disable every configuration for one service."""

        def _run(t: str):
            print_json(internal_post(f"/configurations/{service}/disable-all-configurations", token=t))

        run_with_auth(url, token, _run)

    @app.command("apply")
    def apply_config(
        data: str = typer.Option(..., "--json", help="Config payload JSON string or @file.json"),
        url: str = typer.Option(None, "--url"),
        token: Optional[str] = typer.Option(None, "--token"),
    ):
        """Validate and apply a config payload in memory (not persisted to disk)."""
        payload = _load_json(data)

        def _run(t: str):
            print_json(internal_post("/configurations/apply", token=t, json={"payload": payload}))

        run_with_auth(url, token, _run)

    @app.command("upload")
    def upload_config(
        file_path: Path = typer.Argument(
            ..., exists=True, dir_okay=False, readable=True, help="JSON config file to upload"
        ),
        url: str = typer.Option(None, "--url"),
        token: Optional[str] = typer.Option(None, "--token"),
    ):
        """Upload a config file, validate it, and apply it in memory (not persisted)."""

        def _run(t: str):
            with open(file_path, "rb") as f:
                files = {"file": (file_path.name, f, "application/json")}
                result = internal_post("/configurations/upload", token=t, files=files)
            print_json(result)

        run_with_auth(url, token, _run)
