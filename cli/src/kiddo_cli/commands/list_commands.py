"""List commands: generators, translators, optimizers, evaluators."""

import typer

from kiddo_cli.api.client import internal_get
from kiddo_cli.auth_flow import run_with_auth
from kiddo_cli.output import print_json

def register(app: typer.Typer) -> None:
    """Register list commands on the given Typer app."""

    @app.command("generators")
    def list_generators(
        url: str = typer.Option(None, "--url"),
        token: str | None = typer.Option(None, "--token"),
    ):
        """Show configured generators with activation states."""

        def _run(t: str):
            result = internal_get("/voice/generators", token=t)
            print_json(result)

        run_with_auth(url, token, _run)

    @app.command("translators")
    def list_translators(
        url: str = typer.Option(None, "--url"),
        token: str | None = typer.Option(None, "--token"),
    ):
        """Show configured translators with priority and states."""

        def _run(t: str):
            result = internal_get("/voice/translators", token=t)
            print_json(result)

        run_with_auth(url, token, _run)

    @app.command("optimizers")
    def list_optimizers(
        url: str = typer.Option(None, "--url"),
        token: str | None = typer.Option(None, "--token"),
    ):
        """Show configured optimizers with activation states."""

        def _run(t: str):
            result = internal_get("/voice/optimizers", token=t)
            print_json(result)

        run_with_auth(url, token, _run)

    @app.command("evaluators")
    def list_evaluators(
        url: str = typer.Option(None, "--url"),
        token: str | None = typer.Option(None, "--token"),
    ):
        """Show configured evaluators with activation states."""

        def _run(t: str):
            result = internal_get("/voice/evaluators", token=t)
            print_json(result)

        run_with_auth(url, token, _run)
