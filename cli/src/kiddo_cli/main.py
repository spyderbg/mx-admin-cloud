"""
Kiddo CLI: all commands call the FastAPI backend over HTTP.
Auth: use --token / KIDDO_TOKEN, or auto-login via Unix socket (cli-login) on startup.
"""
import typer

from kiddo_cli.commands import content as content_commands
from kiddo_cli.commands import tasks as tasks_commands

app = typer.Typer()

tasks_app = typer.Typer(help="Task commands: ping, status, operation, list-files.")
content_app = typer.Typer(help="Content commands: categories, category-details, create-object.")

tasks_commands.register(tasks_app)
content_commands.register(content_app)

app.add_typer(tasks_app, name="tasks")
app.add_typer(content_app, name="content")

if __name__ == "__main__":
    app()
