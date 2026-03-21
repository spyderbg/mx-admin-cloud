"""Tasks commands (backend routes_tasks): ping, status, do-operation, list."""

import typer # pylint: disable=import-error

from kiddo_cli.api.client import get, post
from kiddo_cli.auth_flow import run_with_auth
from kiddo_cli.output import print_json, print_json_message


def register(app: typer.Typer) -> None:
    """Register tasks commands on the given Typer app."""

    @app.command()
    def ping():
        """Run a PING from the CLI expected response PONG."""

        result = get("/ping")
        print_json_message(result)

    @app.command()
    def status(
            url: str = typer.Option(None, "--url", help="Backend base URL"),
            token: str | None = typer.Option(None, "--token", help="Bearer token (else auth via CLI socket)"),
    ):
        """Check service status (calls backend API)."""

        def run(t: str):
            result = get("/status", token=t)
            print_json(result)

        run_with_auth(url, token, run)

    @app.command()
    def operation(
            name: str,
            url: str = typer.Option(None, "--url", help="Backend base URL"),
            token: str | None = typer.Option(None, "--token", help="Bearer token (else auth via CLI socket)"),
    ):
        """Perform a service operation (calls backend API)."""

        def run(t: str):
            result = post("/operation", params={"name": name}, token=t)
            print_json(result)

        run_with_auth(url, token, run)

    @app.command(name="list-files")
    def list_files(
            depth: int = typer.Option(1, "--depth", help="Directory depth to list"),
            url: str = typer.Option(None, "--url", help="Backend base URL"),
            token: str | None = typer.Option(None, "--token", help="Bearer token (else auth via CLI socket)"),
    ):
        """Run a report task (calls backend API)."""

        def run(t: str):
            result = post("/list-files", params={"depth": depth}, token=t)
            print_json(result)

        run_with_auth(url, token, run)

