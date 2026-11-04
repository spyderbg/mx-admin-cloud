"""Credential files accept the quoted values used by the repo's .env template."""

import pytest

from test_environment.testenvlib.envfile import load_env_file, parse_env_text, write_env_file


@pytest.mark.parametrize("quote", ["", "'", '"'])
def test_github_token_has_no_surrounding_quotes(tmp_path, quote):
    env_file = tmp_path / ".env"
    env_file.write_text(f"GITHUB_PAT={quote}ghp_test_token{quote}\n")

    assert load_env_file(env_file)["GITHUB_PAT"] == "ghp_test_token"


@pytest.mark.parametrize("quote", ["'", '"'])
def test_quoted_values_preserve_literal_contents(quote):
    value = " spaces # $literal=value "

    assert parse_env_text(f"KEY={quote}{value}{quote}")["KEY"] == value


def test_generated_sandbox_env_round_trip(tmp_path):
    env_file = tmp_path / "test.env"
    values = {"CONTENT_DIR": "/tmp/test content", "EMPTY_KEY": "", "DUMMY_KEY": "test-key"}
    write_env_file(env_file, values, header="Dummy sandbox settings")

    assert load_env_file(env_file) == values
