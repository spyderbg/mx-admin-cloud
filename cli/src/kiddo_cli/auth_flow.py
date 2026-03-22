"""Token resolution and run-with-auth helper for commands."""
import os

import requests
import typer  # pylint: disable=import-error

from kiddo_cli.api import auth as api_auth
from kiddo_cli.output import print_http_auto_failed, print_uds_auth_failed, print_request_error


def get_token_for_run(
        url_option: str | None,
        token_option: str | None,
) -> str:
    """
    Resolve token: use --token / KIDDO_TOKEN if set; otherwise login via Unix socket.
    On socket unavailable, prints formatted error and exits.
    """
    if url_option:
        os.environ["BACKEND_URL"] = url_option
    if token_option is not None:
        if token_option:
            return token_option
        typer.echo("Error: Empty token provided.", err=True)
        raise typer.Exit(1)

    token = os.getenv("KIDDO_TOKEN")
    if token:
        return token

    username = os.getenv("KIDDO_USER", "admin")
    password = os.getenv("KIDDO_PASSWORD", "admin")

    api_base_url = api_auth.get_api_base_url()
    socket_path = api_auth.get_cli_socket_path()

    if os.path.exists(socket_path):
        try:
            return api_auth.login_via_uds(username, password)
        except Exception as e:
            print_uds_auth_failed(socket_path, str(e))
            raise typer.Exit(1)
    else:
        try:
            return api_auth.login(username, password)
        except Exception as e:
            print_http_auto_failed(api_base_url, str(e))
            raise typer.Exit(1)


def run_with_auth(
        url_opt: str | None,
        token_opt: str | None,
        run,
):
    """Resolve token (UDS login if needed), then run the given callable with that token."""
    token = get_token_for_run(url_opt, token_opt)
    try:
        run(token)
    except requests.RequestException as e:
        print_request_error(e)
        raise typer.Exit(1)
