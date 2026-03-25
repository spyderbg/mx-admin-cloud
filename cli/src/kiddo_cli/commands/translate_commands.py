"""Translate commands: translate words or text."""

from pathlib import Path

import json
import typer

from kiddo_cli.api.client import internal_get, internal_post
from kiddo_cli.auth_flow import run_with_auth
from kiddo_cli.output import print_json


def register(app: typer.Typer) -> None:
    """Register translate commands on the given Typer app."""

    @app.command("word")
    def translate_word(
            input_lang: str = typer.Argument(None, help="Input language code"),
            output_lang: str = typer.Argument(None, help="Output language code"),
            word: str = typer.Argument(None, help="Word or text to translate"),
            data: str = typer.Option(None, "--json", help="JSON input string or @file.json"),
            url: str = typer.Option(None, "--url"),
            token: str | None = typer.Option(None, "--token"),
    ):
        """Translate a word or short text from one language to another."""

        # --- Parse JSON if provided ---
        if data:
            if data.startswith("@"):
                with open(data[1:], "r", encoding="utf-8") as f:
                    payload = json.load(f)
            else:
                payload = json.loads(data)
        else:
            # Validate required args if JSON not used
            if not all([input_lang, output_lang, word]):
                raise typer.BadParameter(
                    "Provide either positional arguments or --json"
                )

            payload = {
                "text": word,
                "input_lang_code": input_lang,
                "target_lang_code": output_lang,
            }

        def _run(t: str):
            result = internal_post("/translate", token=t, json=payload)
            print_json(result)

        run_with_auth(url, token, _run)

    @app.command("file")
    def translate_file(
            input_file: Path = typer.Argument(..., help="Path to input file (.txt, .csv, .xlsx)"),
            input_lang: str | None = typer.Option(None, "--input-lang",
                                                  help="Default input language code if not provided in file (e.g., 'en')"),
            output_lang: str = typer.Option("en", "--output-lang", help="Output language code (e.g., 'es')"),
            category: str | None = typer.Option(None, "--category", help="Optional translation category/context"),
            url: str = typer.Option(None, "--url", help="Backend URL"),
            token: str | None = typer.Option(None, "--token", help="Auth token"),
    ):
        """Translate words listed in a file. The backend determines the file format and parsing."""

        def _run(t: str):
            if not input_file.exists() or not input_file.is_file():
                typer.echo(f"Input file not found: {input_file}")
                raise typer.Exit(code=2)

            data: dict[str, str] = {
                "target_lang_code": output_lang,
            }
            if input_lang:
                data["input_lang_code"] = input_lang
            if category:
                data["category"] = category

            file_bytes = input_file.read_bytes()
            files = {"file": (input_file.name, file_bytes, "application/octet-stream")}

            result = internal_post("/translate-file", token=t, data=data, files=files)
            print_json(result)

        run_with_auth(url, token, _run)
