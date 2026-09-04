import pytest

from vb_assistant_bot.config import load_config


def _set_env(monkeypatch, **overrides):
    base = {
        "TELEGRAM_TOKEN": "abc",
        "ADMIN_USER_IDS": "111,222",
        "GROUP_CHAT_ID": "-100123",
        "ALERTS_API_TOKEN": "alerts-token",
    }
    base.update(overrides)
    for key in [
        "TELEGRAM_TOKEN",
        "ADMIN_USER_IDS",
        "GROUP_CHAT_ID",
        "ALERTS_API_TOKEN",
        "ALERTS_REGION_UID",
        "AUTO_PUBLISH_ENABLED",
    ]:
        monkeypatch.delenv(key, raising=False)
    for key, value in base.items():
        if value is not None:
            monkeypatch.setenv(key, value)


def test_load_config_happy_path(monkeypatch):
    _set_env(monkeypatch)
    config = load_config()
    assert config.token == "abc"
    assert config.admin_user_ids == frozenset({111, 222})
    assert config.group_chat_id == -100123
    assert config.alerts_region_uid == "31"
    assert config.auto_publish_enabled is False


def test_load_config_requires_at_least_two_admins(monkeypatch):
    _set_env(monkeypatch, ADMIN_USER_IDS="111")
    with pytest.raises(RuntimeError, match="двох адмінів"):
        load_config()


def test_load_config_rejects_bad_group_chat_id(monkeypatch):
    _set_env(monkeypatch, GROUP_CHAT_ID="not-a-number")
    with pytest.raises(RuntimeError, match="GROUP_CHAT_ID"):
        load_config()


def test_load_config_requires_token(monkeypatch):
    _set_env(monkeypatch, TELEGRAM_TOKEN="")
    with pytest.raises(RuntimeError, match="TELEGRAM_TOKEN"):
        load_config()


def test_load_config_auto_publish_flag(monkeypatch):
    _set_env(monkeypatch, AUTO_PUBLISH_ENABLED="true")
    config = load_config()
    assert config.auto_publish_enabled is True
