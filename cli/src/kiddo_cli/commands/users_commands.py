"""Users commands: list, create, delete, update."""

import typer

from kiddo_cli.api.client import user_mgmt_delete, user_mgmt_get, user_mgmt_patch, user_mgmt_post
from kiddo_cli.auth_flow import run_with_auth
from kiddo_cli.output import print_json

def register(app: typer.Typer) -> None:
    """Register users commands on the given Typer app."""

    @app.command("list")
    def list_users(
        username: str | None = typer.Option(None, "--username", help="Fuzzy search for username"),
        role: str | None = typer.Option(None, "--role", help="Filter by role (admin/viewer)"),
        url: str = typer.Option(None, "--url"),
        token: str | None = typer.Option(None, "--token"),
    ):
        """Show configured users with optional filtering."""

        def _run(t: str):
            params = {}
            if username:
                params["username"] = username
            if role:
                params["role"] = role
            result = user_mgmt_get("/users", token=t, params=params)
            print_json(result)

        run_with_auth(url, token, _run)

    @app.command("create")
    def create_user(
        username: str = typer.Argument(..., help="Username of the new user"),
        password: str = typer.Argument(..., help="Password of the new user"),
        role: str = typer.Option("viewer", "--role", help="Role of the new user (admin/viewer)"),
        url: str = typer.Option(None, "--url"),
        token: str | None = typer.Option(None, "--token"),
    ):
        """Create a new user."""

        def _run(t: str):
            body = {
                "username": username,
                "password": password,
                "role": role
            }
            result = user_mgmt_post("/users", token=t, json=body)
            print_json(result)

        run_with_auth(url, token, _run)

    @app.command("delete")
    def delete_user(
        username: str = typer.Argument(..., help="Username of the user to delete"),
        url: str = typer.Option(None, "--url"),
        token: str | None = typer.Option(None, "--token"),
    ):
        """Delete a user by username."""

        def _run(t: str):
            user_mgmt_delete(f"/users/{username}", token=t)
            print(f"User '{username}' deleted.")

        run_with_auth(url, token, _run)

    @app.command("update")
    def update_user(
        username: str = typer.Argument(..., help="Username of the user to update"),
        password: str | None = typer.Option(None, "--password", help="New password for the user"),
        role: str | None = typer.Option(None, "--role", help="New role for the user (admin/viewer)"),
        url: str = typer.Option(None, "--url"),
        token: str | None = typer.Option(None, "--token"),
    ):
        """Change user password or role by username."""

        def _run(t: str):
            if password:
                user_mgmt_patch(f"/users/{username}/password", token=t, json={"password": password})
                print(f"Password updated for user '{username}'.")
            if role:
                result = user_mgmt_patch(f"/users/{username}/role", token=t, json={"role": role})
                print(f"Role updated for user '{username}'.")
                print_json(result)
            
            if not password and not role:
                print("No updates provided. Use --password or --role.")

        run_with_auth(url, token, _run)
