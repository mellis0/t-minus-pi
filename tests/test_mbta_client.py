from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
import pytest

from src.config import DirectionConfig, RouteConfig
from src.mbta_client import MBTAClient


def test_format_countdown():
    now = datetime.now(timezone.utc)

    # Within 60 seconds -> ARR
    t_arr = now + timedelta(seconds=45)
    minutes, label = MBTAClient._format_countdown(t_arr)
    assert minutes == 0
    assert label == "ARR"

    # 61-119 seconds -> 1 min
    t_1min = now + timedelta(seconds=95)
    minutes, label = MBTAClient._format_countdown(t_1min)
    assert minutes == 1
    assert label == "1 min"

    # 150 seconds -> 2 min
    t_2min = now + timedelta(seconds=150)
    minutes, label = MBTAClient._format_countdown(t_2min)
    assert minutes == 2
    assert label == "2 min"

    # Status Boarding -> BRD
    minutes, label = MBTAClient._format_countdown(t_2min, status_text="Boarding train")
    assert label == "BRD"


@pytest.mark.asyncio
async def test_get_route_departures_mocked():
    client = MBTAClient(api_key="mock_key")

    mock_pred_data = {
        "data": [
            {
                "id": "pred_1",
                "attributes": {
                    "departure_time": (datetime.now(timezone.utc) + timedelta(minutes=4)).isoformat(),
                    "direction_id": 0,
                    "status": "On time",
                },
                "relationships": {
                    "route": {"data": {"id": "Red"}},
                    "trip": {"data": {"id": "trip_1"}},
                },
            }
        ],
        "included": [
            {
                "type": "trip",
                "id": "trip_1",
                "attributes": {"headsign": "Ashmont"},
            },
            {
                "type": "route",
                "id": "Red",
                "attributes": {"color": "DA291C", "short_name": "Red Line"},
            },
        ],
    }

    mock_alert_data = {
        "data": [
            {
                "id": "alert_1",
                "attributes": {
                    "header": "Red Line delay up to 10 minutes",
                    "effect": "DELAY",
                    "severity": 3,
                },
            }
        ]
    }

    with patch.object(client, "fetch_predictions_raw", new_callable=AsyncMock) as mock_fetch_pred:
        with patch.object(client, "fetch_alerts_raw", new_callable=AsyncMock) as mock_fetch_alerts:
            mock_fetch_pred.return_value = mock_pred_data
            mock_fetch_alerts.return_value = mock_alert_data

            route = RouteConfig(
                id="red_line",
                route_id="Red",
                stop_id="place-test",
                name="Red Line",
                directions=[
                    DirectionConfig(direction_id=0, headsign="Ashmont / Braintree"),
                    DirectionConfig(direction_id=1, headsign="Alewife"),
                ],
            )

            result = await client.get_route_departures(route)

            assert result["id"] == "red_line"
            assert result["error"] is None
            assert len(result["alerts"]) == 1
            assert result["alerts"][0]["header"] == "Red Line delay up to 10 minutes"
            assert len(result["directions"]) == 2
            dir_0 = result["directions"][0]
            assert len(dir_0["departures"]) == 1
            assert dir_0["departures"][0]["headsign"] == "Ashmont"
            assert dir_0["departures"][0]["minutes"] == 4
            assert dir_0["departures"][0]["countdown"] == "4 min"
