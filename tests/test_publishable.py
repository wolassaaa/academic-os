import importlib.util
from pathlib import Path

import pytest


spec = importlib.util.spec_from_file_location("check_publishable", Path(__file__).parents[1] / "scripts/check-publishable.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.mark.parametrize("name", ["data/state.sqlite3", "Runtime/config.json", "History/export.md",
                                  ".env.local", "src/oauth-client.json", "config.json", "backup.zip"])
def test_private_files_are_rejected(name):
    assert module.violations(name, b"placeholder")


def test_example_config_and_synthetic_tests_are_allowed():
    assert not module.violations("config.example.json", b'{"secret_ref":"account-handle"}')
    assert not module.violations("tests/test_connections.py", b'{"refresh_token":"test-refresh-token"}')


def test_credential_content_is_rejected_without_disclosing_value():
    value = ("ghp" + "_" + "A" * 36).encode()
    assert module.violations("src/accidental.txt", value) == ["credential-shaped content"]
