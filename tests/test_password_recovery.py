import pytest

from academic_os.config import Settings
from academic_os.db import Database
from academic_os.security import Auth, hash_password


def test_eight_character_password_and_short_password_rejected(tmp_path):
    auth = Auth(Database(tmp_path / "state.sqlite3"), Settings(data_dir=tmp_path))
    auth.set_password("owner", "eight888")
    assert auth.login("owner", "eight888", "test", False)
    with pytest.raises(ValueError):
        hash_password("seven77")


def test_local_reset_invalidates_both_sessions_and_does_not_create_account(tmp_path):
    auth = Auth(Database(tmp_path / "state.sqlite3"), Settings(data_dir=tmp_path))
    auth.set_password("owner", "old-password-value")
    local = auth.login("owner", "old-password-value", "test", False)
    remote = auth.login("owner", "old-password-value", "test", True)
    assert auth.reset_password(None, "eight888") == "owner"
    assert auth.session(local, False) is None
    assert auth.session(remote, True) is None
    with pytest.raises(PermissionError):
        auth.login("owner", "old-password-value", "test", False)
    assert auth.login("owner", "eight888", "test", False)
    with pytest.raises(ValueError):
        auth.reset_password("missing", "eight888")


def test_cli_reset_uses_hidden_confirmation_and_existing_owner(tmp_path, monkeypatch):
    from academic_os import cli
    settings = Settings(data_dir=tmp_path / "data")
    config = tmp_path / "config.json"
    settings.save(config)
    auth = Auth(Database(settings.database_path), settings)
    auth.set_password("custom-owner", "original-password")
    monkeypatch.setattr(cli.getpass, "getpass", lambda _: "eight888")
    assert cli.main(["--config", str(config), "reset-password"]) == 0
    assert auth.login("custom-owner", "eight888", "test", False)
