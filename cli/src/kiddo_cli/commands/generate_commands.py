"""Generate commands: trigger TTS pipeline via backend HTTP."""
from pathlib import Path

import typer

from kiddo_cli.api.client import internal_post, internal_post_bytes
from kiddo_cli.auth_flow import run_with_auth
from kiddo_cli.output import print_json

ALLOWED_EXTENSIONS = {".txt", ".csv", ".xlsx"}


def register(app: typer.Typer) -> None:
    """Register generate commands on the given Typer app."""

    @app.command("run")
    def run(
        input_file: Path = typer.Argument(..., help="Input file (.csv, .xlsx, .txt)"),
        lang: str = typer.Option("en", "-l", "--lang", help="Default language code"),
        output_format: str = typer.Option("mp3", "-f", "--output-format"),
        max_concurrency: int = typer.Option(5, "--max-concurrency"),
        url: str = typer.Option(None, "--url", help="Backend base URL"),
        token: str | None = typer.Option(None, "--token"),
    ):
        """Run the TTS pipeline (translate, generate, evaluate).

        The input file is uploaded to the backend as-is; the backend parses it
        for the job (.txt, .csv, .xlsx).
        """
        if not input_file.exists() or not input_file.is_file():
            typer.echo(f"Error: Input file not found: {input_file}", err=True)
            raise typer.Exit(1)

        suffix = input_file.suffix.lower()
        if suffix not in ALLOWED_EXTENSIONS:
            typer.echo(
                f"Error: Unsupported file type '{suffix}'. "
                f"Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
                err=True,
            )
            raise typer.Exit(1)

        def _run(t: str):
            with open(input_file, "rb") as fh:
                result = internal_post(
                    "/generate-sync",
                    token=t,
                    data={
                        "language": lang,
                        "output_format": output_format,
                        "max_concurrency": str(max_concurrency),
                    },
                    files={"file": (input_file.name, fh)},
                )
            print_json(result)

        run_with_auth(url, token, _run)


    @app.command("word")
    def generate_word(
        word: str = typer.Argument(..., help="Word to generate"),
        lang: str = typer.Option("en", "-l", "--lang", help="Default language code"),
        output_format: str = typer.Option("mp3", "-f", "--output-format"),
        output: Path | None = typer.Option(None, "-o", "--output", help="Output file path"),
        url: str = typer.Option(None, "--url", help="Backend base URL"),
        token: str | None = typer.Option(None, "--token"),
    ):
        """Generate a single word."""

        def _run(t: str):
            audio_bytes = internal_post_bytes(
                "/generators/generate-word",
                token=t,
                json={"word": word, "lang": lang},
            )

            output_file = output or Path(f"{word}.{output_format}")
            output_file.write_bytes(audio_bytes)
            typer.echo(f"Successfully generated word '{word}' and saved to '{output_file}'")

        run_with_auth(url, token, _run)

        
