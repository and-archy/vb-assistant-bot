import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.alerts.in.ua/v1"
_REQUEST_TIMEOUT_SECONDS = 15


@dataclass(frozen=True)
class AlertRecord:
    """Один запис тривоги з alerts.in.ua.

    `raw_alert_type` — категорія сирени (air_raid/artillery_shelling/...),
    НЕ тип озброєння. Тип озброєння (ТЗ п.2: БпЛА/балістика/авіація) API
    таки віддає окремо — масив `threats[].threat_type`
    (drones/ballistic_missiles/cruise_missiles/tactic_aircraft_activity/...,
    https://devs.alerts.in.ua/ → модель Alert). Поле присутнє лише коли
    відкрита хоч одна конкретна загроза — інакше `threats` відсутній і
    `threat_types` тут порожній кортеж."""

    external_id: str
    location_uid: str
    raw_alert_type: str | None
    threat_types: tuple[str, ...]
    started_at: str
    finished_at: str | None
    updated_at: str


class AlertsInUaClient:
    def __init__(self, api_token: str, base_url: str = _BASE_URL) -> None:
        self._api_token = api_token
        self._base_url = base_url

    def fetch_region_history(self, region_uid: str) -> list[AlertRecord]:
        """Історія тривог за останній місяць для регіону (uid) — містить
        started_at/finished_at для завершених тривог, що звільняє від
        необхідності самим вираховувати finished_at з частих опитувань
        /alerts/active.json."""
        url = f"{self._base_url}/regions/{region_uid}/alerts/month_ago.json"
        payload = self._get(url)
        alerts = payload.get("alerts", payload) if isinstance(payload, dict) else payload
        return [self._parse_alert(item) for item in alerts]

    def _get(self, url: str) -> dict | list:
        request = urllib.request.Request(  # noqa: S310
            url, headers={"Authorization": f"Bearer {self._api_token}"}
        )
        try:
            with urllib.request.urlopen(request, timeout=_REQUEST_TIMEOUT_SECONDS) as response:  # noqa: S310
                return json.loads(response.read())
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            logger.error("alerts.in.ua HTTP %s для %s: %s", exc.code, url, body)
            raise
        except urllib.error.URLError as exc:
            logger.error("alerts.in.ua недоступний (%s): %s", url, exc)
            raise

    @staticmethod
    def _parse_alert(item: dict) -> AlertRecord:
        external_id = str(item.get("id") or item.get("alert_id") or "")
        threats = item.get("threats") or []
        threat_types = tuple(t["threat_type"] for t in threats if t.get("threat_type"))
        return AlertRecord(
            external_id=external_id,
            location_uid=str(item.get("location_uid", "")),
            raw_alert_type=item.get("alert_type"),
            threat_types=threat_types,
            started_at=item["started_at"],
            finished_at=item.get("finished_at"),
            updated_at=item.get("updated_at") or item["started_at"],
        )
