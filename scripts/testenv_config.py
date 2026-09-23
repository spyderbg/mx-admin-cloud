#!/usr/bin/env python3
"""Copy backend/config/ into .testenv/config/ and neutralise external providers.

Copies all provider config files verbatim (a missing file is a missing provider, not
a disabled one — the registry builder reads the whole directory), then rewrites the
handful that need to point at the stub instead of the real vendor. See
docs/test_environment/implementation_plan.md §4 for the full rewrite table and the
reasoning behind each entry.

Usage:
    python scripts/testenv_config.py --stub-base-url http://127.0.0.1:48002
    python scripts/testenv_config.py --stub-base-url http://stub:8080 --force
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

from testenvlib import paths  # noqa: E402

RED = "\033[91m"
YELLOW = "\033[93m"
GREEN = "\033[92m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


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

    # -- individual rewrites, one per config file that needs one ----------

    def disable_openai_image_sdk(self) -> None:
        """openai.json is the SDK image provider, prio 1 — would shadow openai_http."""
        def mutate(data: dict) -> None:
            data["service_provider"]["enabled"] = False
        self._edit("text_to_image/openai.json", mutate)

    def enable_openai_http_image(self) -> None:
        def mutate(data: dict) -> None:
            data["service_provider"]["enabled"] = True
            data["service_provider"]["config"]["generate"]["endpoint"] = f"{self.stub}/v1/images/generations"
        self._edit("text_to_image/openai_http.json", mutate)

    def enable_genai_http_image(self) -> None:
        def mutate(data: dict) -> None:
            data["service_provider"]["enabled"] = True
            data["service_provider"]["config"]["generate"]["base_url"] = f"{self.stub}/v1beta/models"
        self._edit("text_to_image/genai_http.json", mutate)

    def enable_comfyui_image(self) -> None:
        def mutate(data: dict) -> None:
            data["service_provider"]["enabled"] = True
            data["service_provider"]["config"]["generate"]["base_url"] = self.stub
        self._edit("text_to_image/comfyui.json", mutate)

    def redirect_speaktor_tts(self) -> None:
        def mutate(data: dict) -> None:
            generate = data["service_provider"]["config"]["generate"]
            generate["endpoint"] = f"{self.stub}/tts"
            generate["response_mode"] = "raw"
            generate.get("headers", {}).pop("Authorization", None)
        self._edit("text_to_speech/speaktor.json", mutate)

    def fallback_static_translate(self) -> None:
        """Make the fallback-to-settings.content_path behaviour explicit rather than
        relying on '../content' resolving to a nonexistent backend/config/content/.
        """
        def mutate(data: dict) -> None:
            data["service_provider"]["config"]["content_dir"] = ""
        self._edit("text_translation/static.json", mutate)

    def enable_seq_logging(self) -> None:
        def mutate(data: dict) -> None:
            seq = data["service_provider"]["config"]["handlers"]["seq"]
            seq["active"] = True
            seq["server_url"] = self.stub
        self._edit("logging/default.json", mutate)


def _copy_config_tree(*, force: bool) -> None:
    if paths.TEST_CONFIG_DIR.exists() and any(paths.TEST_CONFIG_DIR.iterdir()):
        if not force:
            print(
                f"{YELLOW}{paths.testenv_relative(paths.TEST_CONFIG_DIR)} already has content. "
                f"Use --force to rebuild, or `python scripts/testenv.py reset`.{RESET}",
            )
            raise SystemExit(1)
        shutil.rmtree(paths.TEST_CONFIG_DIR)

    shutil.copytree(paths.REAL_CONFIG_DIR, paths.TEST_CONFIG_DIR, symlinks=False)


def _assert_no_symlinks() -> None:
    offenders = [p for p in paths.TEST_CONFIG_DIR.rglob("*") if p.is_symlink()]
    if offenders:
        print(f"{RED}{BOLD}Symlinks found under .testenv/config/ — refusing to proceed:{RESET}")
        for path in offenders:
            print(f"  {RED}{path}{RESET}")
        raise SystemExit(1)


def build(*, stub_base_url: str, force: bool) -> int:
    if not paths.REAL_CONFIG_DIR.is_dir():
        print(f"{RED}Real config directory not found: {paths.REAL_CONFIG_DIR}{RESET}", file=sys.stderr)
        return 1

    print(
        f"{BOLD}Copying {paths.testenv_relative(paths.REAL_CONFIG_DIR)} -> "
        f"{paths.testenv_relative(paths.TEST_CONFIG_DIR)} and redirecting to {stub_base_url} ...{RESET}",
    )
    _copy_config_tree(force=force)

    rewriter = Rewriter(paths.TEST_CONFIG_DIR, stub_base_url)
    rewriter.disable_openai_image_sdk()
    rewriter.enable_openai_http_image()
    rewriter.enable_genai_http_image()
    rewriter.enable_comfyui_image()
    rewriter.redirect_speaktor_tts()
    rewriter.fallback_static_translate()
    rewriter.enable_seq_logging()

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
    args = parser.parse_args()
    return build(stub_base_url=args.stub_base_url, force=args.force)


if __name__ == "__main__":
    raise SystemExit(main())
