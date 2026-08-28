from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, call, patch
import pytest

from src.config import DirectionConfig, RouteConfig
from src.mbta_client import MBTAClient


def test_format_countdown():
    now = datetime.now(timezone.utc)

    # Within 30 seconds -> ARR
    t_arr = now + timedelta(seconds=25)
    minutes, label = MBTAClient._format_countdown(t_arr)
    assert minutes == 0
    assert label == "ARR"

    # 31-119 seconds -> 1 min
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


def test_format_countdown_uses_api_boarding_status():
    now = datetime.now(timezone.utc)
    departure_time = now + timedelta(seconds=30)

    minutes, label = MBTAClient._format_countdown(
        departure_time,
        status_text="Boarding",
    )

    assert minutes == 0
    assert label == "BRD"


def test_format_countdown_uses_vehicle_stopped_at_near_arrival():
    now = datetime.now(timezone.utc)

    minutes, label = MBTAClient._format_countdown(
        now + timedelta(seconds=10),
        vehicle_status="STOPPED_AT",
        vehicle_stop_id="70083",
        prediction_stop_id="70083",
    )

    assert minutes == 0
    assert label == "BRD"


def test_format_countdown_does_not_board_stopped_at_different_stop():
    now = datetime.now(timezone.utc)

    minutes, label = MBTAClient._format_countdown(
        now + timedelta(seconds=10),
        vehicle_status="STOPPED_AT",
        vehicle_stop_id="70081",
        prediction_stop_id="70083",
    )

    assert minutes == 0
    assert label == "ARR"


def test_format_countdown_does_not_board_stopped_at_before_threshold():
    now = datetime.now(timezone.utc)

    minutes, label = MBTAClient._format_countdown(
        now + timedelta(seconds=20),
        vehicle_status="STOPPED_AT",
        vehicle_stop_id="70083",
        prediction_stop_id="70083",
    )

    assert minutes == 0
    assert label == "ARR"


@pytest.mark.asyncio
async def test_fetch_alerts_cached_reuses_fresh_response():
    client = MBTAClient(api_key="mock_key", alert_cache_ttl_seconds=60)
    mock_alert_data = {"data": [{"id": "alert_1"}]}

    with patch.object(client, "fetch_alerts_raw", new_callable=AsyncMock) as mock_fetch_alerts:
        mock_fetch_alerts.return_value = mock_alert_data

        first = await client.fetch_alerts_cached(
            stop_id="place-andrw",
            route_id="Red",
        )
        second = await client.fetch_alerts_cached(
            stop_id="place-andrw",
            route_id="Red",
        )

    assert first == mock_alert_data
    assert second == mock_alert_data
    assert mock_fetch_alerts.await_count == 1


@pytest.mark.asyncio
async def test_get_route_departures_mocked():
    client = MBTAClient(api_key="mock_key")

    mock_pred_data = {
        "data": [
            {
                "id": "pred_1",
                "attributes": {
                    "departure_time": (datetime.now(timezone.utc) + timedelta(minutes=4, seconds=5)).isoformat(),
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
            mock_fetch_pred.side_effect = [mock_pred_data, {"data": [], "included": []}]
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
            mock_fetch_pred.assert_has_awaits([
                call(
                    stop_id="place-test",
                    route_id="Red",
                    route_filter=[],
                    direction_id=0,
                    page_limit=4,
                ),
                call(
                    stop_id="place-test",
                    route_id="Red",
                    route_filter=[],
                    direction_id=1,
                    page_limit=4,
                ),
            ])


def test_merge_prediction_data_deduplicates_included_resources():
    merged = MBTAClient._merge_prediction_data([
        {
            "data": [{"id": "prediction-1"}],
            "included": [
                {"type": "route", "id": "Red"},
                {"type": "trip", "id": "trip-1"},
            ],
        },
        {
            "data": [{"id": "prediction-2"}],
            "included": [
                {"type": "route", "id": "Red"},
                {"type": "trip", "id": "trip-2"},
            ],
        },
    ])

    assert merged["data"] == [{"id": "prediction-1"}, {"id": "prediction-2"}]
    assert merged["included"] == [
        {"type": "route", "id": "Red"},
        {"type": "trip", "id": "trip-1"},
        {"type": "trip", "id": "trip-2"},
    ]


@pytest.mark.asyncio
async def test_get_route_departures_prefers_arrival_time_over_departure_time():
    client = MBTAClient(api_key="mock_key")
    now = datetime.now(timezone.utc)
    arrival_time = now + timedelta(minutes=1, seconds=30)
    departure_time = now + timedelta(minutes=2)

    mock_pred_data = {
        "data": [
            {
                "id": "pred_1",
                "attributes": {
                    "arrival_time": arrival_time.isoformat(),
                    "departure_time": departure_time.isoformat(),
                    "direction_id": 0,
                    "status": None,
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
    mock_alert_data = {"data": []}

    with patch.object(client, "fetch_predictions_raw", new_callable=AsyncMock) as mock_fetch_pred:
        with patch.object(client, "fetch_alerts_raw", new_callable=AsyncMock) as mock_fetch_alerts:
            mock_fetch_pred.return_value = mock_pred_data
            mock_fetch_alerts.return_value = mock_alert_data

            route = RouteConfig(
                id="red_line",
                route_id="Red",
                stop_id="place-test",
                name="Red Line",
                directions=[DirectionConfig(direction_id=0, headsign="Ashmont")],
            )

            result = await client.get_route_departures(route)
            dep = result["directions"][0]["departures"][0]

            assert dep["time_iso"] == arrival_time.isoformat()
            assert dep["arrival_time_iso"] == arrival_time.isoformat()
            assert dep["departure_time_iso"] == departure_time.isoformat()
