import io
import json
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from vb_assistant_bot.alerts_client import AlertsInUaClient


def _fake_response(payload):
    body = json.dumps(payload).encode("utf-8")
    cm = MagicMock()
    cm.__enter__.return_value = io.BytesIO(body)
    cm.__exit__.return_value = False
    return cm


def test_fetch_region_history_parses_list_payload():
    payload = [
        {
            "id": 123,
            "location_uid": "31",
            "alert_type": "air_raid",
            "started_at": "2026-01-14T20:00:00.000Z",
            "finished_at": "2026-01-14T21:00:00.000Z",
            "updated_at": "2026-01-14T21:00:00.000Z",
        }
    ]
    client = AlertsInUaClient("token")
    with patch("urllib.request.urlopen", return_value=_fake_response(payload)):
        records = client.fetch_region_history("31")

    assert len(records) == 1
    assert records[0].external_id == "123"
    assert records[0].location_uid == "31"
    assert records[0].raw_alert_type == "air_raid"
    assert records[0].finished_at == "2026-01-14T21:00:00.000Z"


def test_fetch_region_history_parses_wrapped_payload():
    payload = {
        "alerts": [
            {
                "id": 1,
                "location_uid": "31",
                "alert_type": "air_raid",
                "started_at": "2026-01-14T20:00:00.000Z",
                "finished_at": None,
                "updated_at": "2026-01-14T20:00:00.000Z",
            }
        ]
    }
    client = AlertsInUaClient("token")
    with patch("urllib.request.urlopen", return_value=_fake_response(payload)):
        records = client.fetch_region_history("31")

    assert len(records) == 1
    assert records[0].finished_at is None


def test_get_raises_and_logs_on_http_error():
    client = AlertsInUaClient("bad-token")
    error = urllib.error.HTTPError(
        "url", 401, "Unauthorized", {}, io.BytesIO(b'{"message": "Invalid API token."}')
    )
    with patch("urllib.request.urlopen", side_effect=error), pytest.raises(urllib.error.HTTPError):
        client.fetch_region_history("31")
