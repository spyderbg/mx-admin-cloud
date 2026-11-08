"""Logging commands: emit structured log messages through the backend logging service."""

import json
from typing import Optional

import typer

from kiddo_cli.api.client import internal_post
from kiddo_cli.auth_flow import run_with_auth
from kiddo_cli.output import print_json


def register(app: typer.Typer) -> None:
    """Register logging commands on the given Typer app."""

    def _build_log_payload(
            event: Optional[str],
            level: str,
            fields: Optional[str],
            data: Optional[str],
    ) -> dict:
        if data:
            if data.startswith("@"):
                with open(data[1:], "r", encoding="utf-8") as f:
                    return json.load(f)
            return json.loads(data)

        if not event:
            raise typer.BadParameter(
                "Provide either the positional argument (event) or --json"
            )

        return {
            "level": level,
            "event": event,
            "fields": json.loads(fields) if fields else {},
        }

    @app.command("emit")
    def emit_log(
            event: str = typer.Argument(
                None,
                help="Log event",
            ),
            *,
            level: str = typer.Option(
                "info",
                "--level",
                help="Log level: debug, info, warning, error, critical",
            ),
            fields: Optional[str] = typer.Option(
                None,
                "--fields",
                help="Extra fields as a JSON object string (e.g. '{\"key\": \"value\"}')",
            ),
            data: Optional[str] = typer.Option(
                None,
                "--json",
                help="Full JSON body string or @file.json",
            ),
            url: Optional[str] = typer.Option(
                None,
                "--url",
                help="Backend URL",
            ),
            token: Optional[str] = typer.Option(
                None,
                "--token",
                help="Auth token",
            ),
    ):
        """Emit a structured log message through the default logger."""

        payload = _build_log_payload(event, level, fields, data)

        def _run(t: str):
            result = internal_post("/loggers/emit", token=t, json=payload)
            print_json(result)

        run_with_auth(url, token, _run)

    @app.command("log")
    def emit_log_by_name(
            logger_name: str = typer.Argument(
                ...,
                help="Logger name (target channel)",
            ),
            event: str = typer.Argument(
                None,
                help="Log event",
            ),
            *,
            level: str = typer.Option(
                "info",
                "--level",
                help="Log level: debug, info, warning, error, critical",
            ),
            fields: Optional[str] = typer.Option(
                None,
                "--fields",
                help="Extra fields as a JSON object string",
            ),
            data: Optional[str] = typer.Option(
                None,
                "--json",
                help="Full JSON body string or @file.json",
            ),
            url: Optional[str] = typer.Option(
                None,
                "--url",
                help="Backend URL",
            ),
            token: Optional[str] = typer.Option(
                None,
                "--token",
                help="Auth token",
            ),
    ):
        """Emit a structured log message to a specific logger."""

        payload = _build_log_payload(event, level, fields, data)

        def _run(t: str):
            result = internal_post(f"/loggers/{logger_name}/log", token=t, json=payload)
            print_json(result)

        run_with_auth(url, token, _run)

    @app.command("api-request")
    def emit_api_request(
            event: str = typer.Argument(
                None,
                help="Log event",
            ),
            *,
            level: str = typer.Option(
                "info",
                "--level",
                help="Log level: debug, info, warning, error, critical",
            ),
            fields: Optional[str] = typer.Option(
                None,
                "--fields",
                help="Extra fields as a JSON object string",
            ),
            data: Optional[str] = typer.Option(
                None,
                "--json",
                help="Full JSON body string or @file.json",
            ),
            url: Optional[str] = typer.Option(
                None,
                "--url",
                help="Backend URL",
            ),
            token: Optional[str] = typer.Option(
                None,
                "--token",
                help="Auth token",
            ),
    ):
        """Emit an API request log message to the 'requests' logger."""

        payload = _build_log_payload(event, level, fields, data)

        def _run(t: str):
            result = internal_post("/loggers/logging/log/api-request", token=t, json=payload)
            print_json(result)

        run_with_auth(url, token, _run)

    @app.command("service")
    def emit_service_log(
            event: str = typer.Argument(
                None,
                help="Log event",
            ),
            *,
            level: str = typer.Option(
                "info",
                "--level",
                help="Log level: debug, info, warning, error, critical",
            ),
            service: str = typer.Option(
                "cli",
                "--service",
                help="Service name recorded on the log message",
            ),
            fields: Optional[str] = typer.Option(
                None,
                "--fields",
                help="Extra fields as a JSON object string",
            ),
            data: Optional[str] = typer.Option(
                None,
                "--json",
                help="Full JSON body string or @file.json",
            ),
            url: Optional[str] = typer.Option(
                None,
                "--url",
                help="Backend URL",
            ),
            token: Optional[str] = typer.Option(
                None,
                "--token",
                help="Auth token",
            ),
    ):
        """Emit a service log message to the 'service' logger."""

        payload = _build_log_payload(event, level, fields, data)
        payload.setdefault("service", service)

        def _run(t: str):
            result = internal_post("/loggers/logging/log/service", token=t, json=payload)
            print_json(result)

        run_with_auth(url, token, _run)

    @app.command("provider-request")
    def log_provider_request(
            *,
            provider: Optional[str] = typer.Option(
                None,
                "--provider",
                help="TTS/STT provider name",
            ),
            model: Optional[str] = typer.Option(
                None,
                "--model",
                help="Provider model identifier",
            ),
            actor: Optional[str] = typer.Option(
                None,
                "--actor",
                help="Actor name",
            ),
            voice_id: Optional[str] = typer.Option(
                None,
                "--voice-id",
                help="Voice identifier",
            ),
            language: Optional[str] = typer.Option(
                None,
                "--language",
                help="Language code",
            ),
            input_data: Optional[str] = typer.Option(
                None,
                "--input",
                help="Input text or data passed to the provider",
            ),
            response_duration: Optional[float] = typer.Option(
                None,
                "--response-duration",
                help="Provider response latency in seconds",
            ),
            data: Optional[str] = typer.Option(
                None,
                "--json",
                help="Full JSON body string or @file.json",
            ),
            url: Optional[str] = typer.Option(
                None,
                "--url",
                help="Backend URL",
            ),
            token: Optional[str] = typer.Option(
                None,
                "--token",
                help="Auth token",
            ),
    ):
        """Log a TTS/STT provider request (model, actor, latency, …)."""

        if data:
            if data.startswith("@"):
                with open(data[1:], "r", encoding="utf-8") as f:
                    payload = json.load(f)
            else:
                payload = json.loads(data)
        else:
            payload = {}
            if provider is not None:
                payload["provider"] = provider
            if model is not None:
                payload["model"] = model
            if actor is not None:
                payload["actor"] = actor
            if voice_id is not None:
                payload["voice_id"] = voice_id
            if language is not None:
                payload["language"] = language
            if input_data is not None:
                payload["input"] = input_data
            if response_duration is not None:
                payload["response_duration"] = response_duration

        def _run(t: str):
            result = internal_post("/loggers/logging/log/provider", token=t, json=payload)
            print_json(result)

        run_with_auth(url, token, _run)

    @app.command("payment")
    def emit_payment_log(
            event: str = typer.Argument(
                None,
                help="Log event",
            ),
            *,
            level: str = typer.Option(
                "info",
                "--level",
                help="Log level: debug, info, warning, error, critical",
            ),
            fields: Optional[str] = typer.Option(
                None,
                "--fields",
                help="Extra fields as a JSON object string",
            ),
            data: Optional[str] = typer.Option(
                None,
                "--json",
                help="Full JSON body string or @file.json",
            ),
            url: Optional[str] = typer.Option(
                None,
                "--url",
                help="Backend URL",
            ),
            token: Optional[str] = typer.Option(
                None,
                "--token",
                help="Auth token",
            ),
    ):
        """Emit a payment log message to the 'payment' logger."""

        payload = _build_log_payload(event, level, fields, data)

        def _run(t: str):
            result = internal_post("/loggers/logging/log/payment", token=t, json=payload)
            print_json(result)

        run_with_auth(url, token, _run)

    @app.command("reset")
    def reset_loggers(
            url: Optional[str] = typer.Option(
                None,
                "--url",
                help="Backend URL",
            ),
            token: Optional[str] = typer.Option(
                None,
                "--token",
                help="Auth token",
            ),
    ):
        """Reset active logging providers."""

        def _run(t: str):
            result = internal_post("/loggers/reset", token=t, json={})
            print_json(result)

        run_with_auth(url, token, _run)
