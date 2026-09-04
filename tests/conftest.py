from pathlib import Path

import pytest

from vb_assistant_bot import db
from vb_assistant_bot.config import Config
from vb_assistant_bot.content import load_texts, load_thresholds

_PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def conn():
    connection = db.init_db(":memory:")
    yield connection
    connection.close()


@pytest.fixture
def config():
    return Config(
        token="test-token",
        admin_user_ids=frozenset({111, 222}),
        group_chat_id=-100999,
        alerts_api_token="test-alerts-token",
        alerts_region_uid="31",
        db_path=":memory:",
        timezone="Europe/Kyiv",
        texts_config_path=str(_PROJECT_ROOT / "config" / "texts.json"),
        thresholds_config_path=str(_PROJECT_ROOT / "config" / "thresholds.json"),
        auto_publish_enabled=False,
    )


@pytest.fixture
def texts():
    return load_texts(str(_PROJECT_ROOT / "config" / "texts.json"))


@pytest.fixture
def thresholds():
    return load_thresholds(str(_PROJECT_ROOT / "config" / "thresholds.json"))
