"""
Kiddo CLI: all commands call the FastAPI backend over HTTP.
Auth: use --token / KIDDO_TOKEN, or auto-login via Unix socket (cli-login) on startup.
"""

import typer # pylint: disable=import-error

from kiddo_cli.commands import content as content_commands
from kiddo_cli.commands import generate as generate_commands
from kiddo_cli.commands import list_commands as list_commands
from kiddo_cli.commands import tasks as tasks_commands

app = typer.Typer(no_args_is_help=True)

tasks_app = typer.Typer(help="Task commands: ping, status, operation, list-files.")
content_app = typer.Typer(help="Content commands: categories, category-details, create-object.")
list_app = typer.Typer(help="List configured components: generators, translators, optimizers, evaluators.")
generate_app = typer.Typer(help="Voice generation: run pipeline.")

tasks_commands.register(tasks_app)
content_commands.register(content_app)
list_commands.register(list_app)
generate_commands.register(generate_app)

app.add_typer(tasks_app, name="tasks")
app.add_typer(content_app, name="content")
app.add_typer(list_app, name="list")
app.add_typer(generate_app, name="generate")

if __name__ == "__main__":
    app()
