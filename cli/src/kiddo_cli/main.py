"""
Kiddo CLI: all commands call the FastAPI backend over HTTP.
Auth: use --token / KIDDO_TOKEN, or auto-login via Unix socket (cli-login) on startup.
"""

import os

import typer # pylint: disable=import-error

from kiddo_cli.commands import content_commands as content_commands  # pylint: disable=import-error
from kiddo_cli.commands import generate_commands as generate_commands  # pylint: disable=import-error
from kiddo_cli.commands import list_commands as list_commands  # pylint: disable=import-error
from kiddo_cli.commands import common_commands as tasks_commands  # pylint: disable=import-error
from kiddo_cli.commands import translate_commands as translate_commands  # pylint: disable=import-error
from kiddo_cli.commands import users_commands as users_commands  # pylint: disable=import-error
from kiddo_cli.commands import logging_commands as logging_commands  # pylint: disable=import-error

app = typer.Typer(no_args_is_help=True)

common_app = typer.Typer(help="Task commands: ping, status, operation, list-files.")
content_app = typer.Typer(help="Content commands: categories, category-details, create-item.")
list_app = typer.Typer(help="List configured components: generators, translators, optimizers, evaluators.")
translate_app = typer.Typer(help="Translation commands: translate words.")
users_app = typer.Typer(help="User management: list, create, delete, update.")
generate_app = typer.Typer(help="Voice generation: run pipeline.")
logging_app = typer.Typer(help="Logging commands: emit, api-request, provider-request, translator-request.")


@app.callback()
def main(
        verbose: bool = typer.Option(
            False,
            "--verbose",
            "-v",
            help="Enable verbose CLI output (HTTP/debug messages).",
        ),
) -> None:
    """Root CLI options."""
    os.environ["KIDDO_CLI_VERBOSE"] = "1" if verbose else "0"

tasks_commands.register(common_app)
content_commands.register(content_app)
list_commands.register(list_app)
generate_commands.register(generate_app)
translate_commands.register(translate_app)
users_commands.register(users_app)
logging_commands.register(logging_app)

app.add_typer(common_app, name="common")
app.add_typer(content_app, name="content")
app.add_typer(list_app, name="list")
app.add_typer(generate_app, name="generate")
app.add_typer(translate_app, name="translate")
app.add_typer(users_app, name="users")
app.add_typer(logging_app, name="logging")

if __name__ == "__main__":
    app()
