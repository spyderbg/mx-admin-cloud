#!/usr/bin/env python3
"""Copy a config directory into .testenv/config/ and neutralise external providers.

The source is backend/config/ unless --config-dir / $TESTENV_CONFIG_SOURCE / the last
seed's recorded choice says otherwise (testenvlib/sources.py). Copies all provider
config files verbatim (a missing file is a missing provider, not
a disabled one — the registry builder reads the whole directory), then rewrites the
handful that need to point at the stub instead of the real vendor. See
docs/test_environment/implementation_plan.md §4 for the full rewrite table and the
reasoning behind each entry.

Usage:
    python test_environment/testenv_config.py --stub-base-url http://127.0.0.1:48002
    python test_environment/testenv_config.py --stub-base-url http://stub:8080 --force
    python test_environment/testenv_config.py --config-dir /path/to/config --force
"""

from __future__ import annotations

import argparse
import copy
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.stdout.reconfigure(line_buffering=True)  # see testenv.py for why

from testenvlib import paths, sources  # noqa: E402

RED = "\033[91m"
YELLOW = "\033[93m"
GREEN = "\033[92m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"

# Provider types the stub (or an offline implementation) covers, per external service.
# Every other enabled type in that service is disabled: it would call a real API.
STUB_COVERED: dict[str, set[str]] = {
    "text_to_image": {"openai_http", "genai_http", "comfyui"},
    "text_to_speech": {"speaktor"},
    "text_translation": {"openai_translate", "qwen_translate", "static_translate"},
}
# Service directories handled here: the three above, logging (sinks redirected), plus
# content and voice_optimizers, which are local (file system, ffmpeg/pydub).
KNOWN_SERVICES = set(STUB_COVERED) | {"logging", "content", "voice_optimizers"}


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _provider_type(path: Path) -> str:
    """Return a config's provider type normalised as the backend reads it, or '' if unreadable.

    The backend skips a config that isn't a JSON object with a service_provider object, so
    such a file is no provider of any type here either.
    """
    try:
        envelope = _load(path).get("service_provider")
    except (OSError, ValueError, AttributeError):
        return ""
    if not isinstance(envelope, dict):
        return ""
    return str(envelope.get("provider", "")).strip().lower()


def _dump(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


class Rewriter:
    """Applies one config rewrite and records a one-line human summary."""

    def __init__(self, config_dir: Path, stub_base_url: str) -> None:
        self.config_dir = config_dir
        self.stub = stub_base_url.rstrip("/")
        self.summary: list[str] = []

    def _path(self, relative: str) -> Path:
        return self.config_dir / relative

    def _edit(self, relative: str, mutate) -> None:  # noqa: ANN001
        path = self._path(relative)
        if not path.is_file():
            self.summary.append(f"{YELLOW}  ! missing, skipped: {relative}{RESET}")
            return
        data = _load(path)
        before = copy.deepcopy(data)
        mutate(data)
        if data != before:
            _dump(path, data)
            self.summary.append(f"{GREEN}  rewrote {relative}{RESET}")
        else:
            self.summary.append(f"{DIM}  unchanged {relative}{RESET}")

    def _configs_of(self, service: str, provider_types: set[str] | None = None, *, exclude: bool = False) -> list[str]:
        """Config files under *service*/ whose provider type is (or with exclude=True,
        isn't) in *provider_types*, as paths relative to the config dir. Matched by
        provider type, never by file name: a config directory may name its files
        anything (one config per language pair, a second "slow" variant, ...).
        """
        directory = self.config_dir / service
        if not directory.is_dir():
            return []
        out = []
        for path in sorted(directory.glob("*.json")):
            ptype = _provider_type(path)
            if not ptype:
                continue
            if provider_types is None or ((ptype in provider_types) != exclude):
                out.append(f"{service}/{path.name}")
        return out

    def _edit_type(self, service: str, provider_type: str, mutate) -> None:  # noqa: ANN001
        configs = self._configs_of(service, {provider_type})
        if not configs:
            self.summary.append(f"{DIM}  no {provider_type} configs in {service}/{RESET}")
        for relative in configs:
            self._edit(relative, mutate)

    @staticmethod
    def _disable(data: dict) -> None:
        data["service_provider"]["enabled"] = False

    # -- individual rewrites -------------------------------------------------

    def disable_unredirectable_image_providers(self) -> None:
        """Only openai_http, genai_http and comfyui take a configurable URL. The SDK
        providers (openai, genai) hardcode theirs — openai.json is enabled at priority 1
        in the repo config and would shadow openai_http — so every other type is off.
        """
        for relative in self._configs_of("text_to_image", STUB_COVERED["text_to_image"], exclude=True):
            self._edit(relative, self._disable)

    def enable_openai_http_image(self) -> None:
        def mutate(data: dict) -> None:
            data["service_provider"]["enabled"] = True
            data["service_provider"]["config"]["generate"]["endpoint"] = f"{self.stub}/v1/images/generations"
        self._edit_type("text_to_image", "openai_http", mutate)

    def enable_genai_http_image(self) -> None:
        def mutate(data: dict) -> None:
            data["service_provider"]["enabled"] = True
            data["service_provider"]["config"]["generate"]["base_url"] = f"{self.stub}/v1beta/models"
        self._edit_type("text_to_image", "genai_http", mutate)

    def enable_comfyui_image(self) -> None:
        def mutate(data: dict) -> None:
            data["service_provider"]["enabled"] = True
            data["service_provider"]["config"]["generate"]["base_url"] = self.stub
        self._edit_type("text_to_image", "comfyui", mutate)

    def isolate_tts_to_speaktor(self) -> None:
        """Disable every text_to_speech config that isn't a speaktor one.

        speaktor is the only TTS provider the stub covers. The source config decides which
        providers are enabled, and that changes over time (the repo config enables
        elevenlabs and pyttsx3, not speaktor). Leaving any other one enabled would send
        sandbox TTS to a real API (elevenlabs/openai hardcode their endpoints) or to a
        local engine, bypassing the stub.
        """
        for relative in self._configs_of("text_to_speech", STUB_COVERED["text_to_speech"], exclude=True):
            self._edit(relative, self._disable)

    def redirect_speaktor_tts(self) -> None:
        def mutate(data: dict) -> None:
            data["service_provider"]["enabled"] = True
            generate = data["service_provider"]["config"]["generate"]
            generate["endpoint"] = f"{self.stub}/tts"
            generate["response_mode"] = "raw"
            generate.get("headers", {}).pop("Authorization", None)
        self._edit_type("text_to_speech", "speaktor", mutate)

    def disable_unredirectable_translation_providers(self) -> None:
        """openai_translate (via OPENAI_BASE_URL), qwen_translate (base_url) and the
        offline static_translate are covered; any other type has no seam to redirect.
        """
        for relative in self._configs_of("text_translation", STUB_COVERED["text_translation"], exclude=True):
            self._edit(relative, self._disable)

    def fallback_static_translate(self) -> None:
        """Make the fallback-to-settings.content_path behaviour explicit rather than
        relying on '../content' resolving to a nonexistent backend/config/content/.
        """
        def mutate(data: dict) -> None:
            data["service_provider"]["config"]["content_dir"] = ""
        self._edit_type("text_translation", "static_translate", mutate)

    def redirect_qwen_translate(self) -> None:
        """Point every qwen_translate config's base_url at the translation stub.

        Set unconditionally: a config without a base_url falls back to the provider's
        default, which is the real self-hosted Qwen server.
        """
        def mutate(data: dict) -> None:
            envelope = data["service_provider"]
            if not isinstance(envelope.get("config"), dict):
                envelope["config"] = {}  # the backend reads a missing or non-object config as {}
            envelope["config"]["base_url"] = f"{self.stub}/v1/"
        self._edit_type("text_translation", "qwen_translate", mutate)

    def enable_seq_logging(self) -> None:
        def mutate(data: dict) -> None:
            seq = data["service_provider"]["config"]["handlers"]["seq"]
            seq["active"] = True
            seq["server_url"] = self.stub
        self._edit("logging/default.json", mutate)

    def redirect_all_log_sinks(self) -> None:
        """Point every logging handler that has a server_url (a Seq sink) at the stub.

        enable_seq_logging only covers default.json's "seq" handler, but a handler's
        "active" flag defaults to true: the repo's service.json carries a live
        "service_seq" handler aimed at http://localhost:5341, a developer's real local
        Seq. Its active flag is left as it is; only the destination changes.
        """
        def mutate(data: dict) -> None:
            config = data["service_provider"].get("config")
            handlers = config.get("handlers") if isinstance(config, dict) else None
            if not isinstance(handlers, dict):
                return
            for handler in handlers.values():
                if isinstance(handler, dict) and "server_url" in handler:
                    handler["server_url"] = self.stub
        for relative in self._configs_of("logging"):
            self._edit(relative, mutate)

    def warn_unknown_services(self) -> None:
        """Flag service directories this rewriter has no rules for — nothing in them is
        redirected, so an enabled external provider there would reach the real thing.
        """
        for entry in sorted(self.config_dir.iterdir()):
            if entry.is_dir() and entry.name not in KNOWN_SERVICES:
                self.summary.append(
                    f"{YELLOW}  ! {entry.name}/ is not a service this tool knows how to redirect — "
                    f"copied as-is; check it calls nothing external{RESET}",
                )


def _copy_config_tree(source_dir: Path, *, force: bool) -> None:
    if paths.TEST_CONFIG_DIR.exists() and any(paths.TEST_CONFIG_DIR.iterdir()):
        if not force:
            print(
                f"{YELLOW}{paths.testenv_relative(paths.TEST_CONFIG_DIR)} already has content. "
                f"Use --force to rebuild, or `python test_environment/testenv.py reset`.{RESET}",
            )
            raise SystemExit(1)
        shutil.rmtree(paths.TEST_CONFIG_DIR)

    shutil.copytree(source_dir, paths.TEST_CONFIG_DIR, symlinks=False)


def _assert_no_symlinks() -> None:
    offenders = [p for p in paths.TEST_CONFIG_DIR.rglob("*") if p.is_symlink()]
    if offenders:
        print(f"{RED}{BOLD}Symlinks found under .testenv/config/ — refusing to proceed:{RESET}")
        for path in offenders:
            print(f"  {RED}{path}{RESET}")
        raise SystemExit(1)


def build(*, stub_base_url: str, force: bool, config_dir: Path | None = None) -> int:
    """Copy *config_dir* (default: sources.resolve()'s config) and neutralise it."""
    if config_dir is None:
        try:
            config_dir = sources.resolve().config_dir
        except sources.SourceError as exc:
            print(f"{RED}{exc}{RESET}", file=sys.stderr)
            return 1
    if not config_dir.is_dir():
        print(f"{RED}Config directory not found: {config_dir}{RESET}", file=sys.stderr)
        return 1

    print(
        f"{BOLD}Copying {paths.testenv_relative(config_dir)} -> "
        f"{paths.testenv_relative(paths.TEST_CONFIG_DIR)} and redirecting to {stub_base_url} ...{RESET}",
    )
    _copy_config_tree(config_dir, force=force)

    rewriter = Rewriter(paths.TEST_CONFIG_DIR, stub_base_url)
    rewriter.disable_unredirectable_image_providers()
    rewriter.enable_openai_http_image()
    rewriter.enable_genai_http_image()
    rewriter.enable_comfyui_image()
    rewriter.isolate_tts_to_speaktor()
    rewriter.redirect_speaktor_tts()
    rewriter.disable_unredirectable_translation_providers()
    rewriter.fallback_static_translate()
    rewriter.redirect_qwen_translate()
    rewriter.enable_seq_logging()
    rewriter.redirect_all_log_sinks()
    rewriter.warn_unknown_services()

    _assert_no_symlinks()

    for line in rewriter.summary:
        print(line)
    print(f"{GREEN}Config neutralisation complete.{RESET}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--stub-base-url",
        default=f"http://127.0.0.1:{paths.NATIVE_DEFAULT_STUB_PORT}",
        help="Base URL the backend can reach the stub at (e.g. http://stub:8080 in Docker mode).",
    )
    parser.add_argument("--force", action="store_true", help="wipe an existing .testenv/config and rebuild")
    parser.add_argument(
        "--config-dir", default=None,
        help=f"copy this config directory (default: ${sources.CONFIG_ENV_VAR}, then the last seed's choice, "
             "then backend/config/)",
    )
    args = parser.parse_args()
    try:
        source = sources.resolve(config_dir=args.config_dir)
    except sources.SourceError as exc:
        print(f"{RED}{exc}{RESET}", file=sys.stderr)
        return 1
    return build(stub_base_url=args.stub_base_url, force=args.force, config_dir=source.config_dir)


if __name__ == "__main__":
    raise SystemExit(main())
