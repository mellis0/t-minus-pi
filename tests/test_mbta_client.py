from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, call, patch
import pytest

from src.config import DirectionConfig, RouteConfig
from src.mbta_client import MBTAClient


class MockResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


class MockAsyncClient:
    def __init__(self, payload):
        self.get = AsyncMock(return_value=MockResponse(payload))

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        pass


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


def test_get_schedule_time_filters_uses_current_service_day():
    filters = MBTAClient._get_schedule_time_filters(
        datetime(2026, 8, 28, 21, 42, tzinfo=timezone.utc)
    )

    assert filters == {
        "filter[date]": "2026-08-28",
        "filter[min_time]": "17:42",
    }


def test_get_schedule_time_filters_handles_after_midnight_service_day():
    filters = MBTAClient._get_schedule_time_filters(
        datetime(2026, 8, 28, 5, 15, tzinfo=timezone.utc)
    )

    assert filters == {
        "filter[date]": "2026-08-27",
        "filter[min_time]": "25:15",
    }


def test_commuter_rail_service_detail_detects_worcester_express_segment():
    trip_stops = [
        {"id": "NEC-2287", "name": "South Station"},
        {"id": "WML-0012-07", "name": "Back Bay"},
        {"id": "WML-0025-07", "name": "Lansdowne"},
        {"id": "WML-0035-01", "name": "Boston Landing"},
        {"id": "WML-0199-02", "name": "West Natick"},
        {"id": "WML-0214-02", "name": "Framingham"},
    ]

    assert MBTAClient._commuter_rail_service_detail(
        "CR-Worcester",
        0,
        trip_stops,
    ) == "Express to West Natick after Boston Landing"


def test_commuter_rail_service_detail_detects_worcester_local():
    trip_stops = [
        {"id": "NEC-2287", "name": "South Station"},
        {"id": "WML-0012-07", "name": "Back Bay"},
        {"id": "WML-0025-07", "name": "Lansdowne"},
        {"id": "WML-0035-01", "name": "Boston Landing"},
        {"id": "WML-0081-02", "name": "Newtonville"},
        {"id": "WML-0091-02", "name": "West Newton"},
        {"id": "WML-0102-02", "name": "Auburndale"},
        {"id": "WML-0125-02", "name": "Wellesley Farms"},
        {"id": "WML-0135-02", "name": "Wellesley Hills"},
        {"id": "WML-0147-02", "name": "Wellesley Square"},
        {"id": "WML-0177-02", "name": "Natick Center"},
        {"id": "WML-0199-02", "name": "West Natick"},
        {"id": "WML-0214-02", "name": "Framingham"},
    ]

    assert MBTAClient._commuter_rail_service_detail(
        "CR-Worcester",
        0,
        trip_stops,
    ) == "Local"


def test_commuter_rail_service_detail_uses_stop_names_across_platform_ids():
    trip_stops = [
        {"id": "NEC-2287", "name": "South Station"},
        {"id": "WML-0012-07", "name": "Back Bay"},
        {"id": "WML-0025-07", "name": "Lansdowne"},
        {"id": "WML-0035-01", "name": "Boston Landing"},
        {"id": "WML-0081-02", "name": "Newtonville"},
        {"id": "WML-0091-02", "name": "West Newton"},
        {"id": "WML-0102-02", "name": "Auburndale"},
        {"id": "WML-0125-01", "name": "Wellesley Farms"},
        {"id": "WML-0135-01", "name": "Wellesley Hills"},
        {"id": "WML-0147-01", "name": "Wellesley Square"},
        {"id": "WML-0177-01", "name": "Natick Center"},
        {"id": "WML-0199-01", "name": "West Natick"},
        {"id": "WML-0214-01", "name": "Framingham"},
        {"id": "WML-0252-01", "name": "Ashland"},
        {"id": "WML-0274-01", "name": "Southborough"},
        {"id": "WML-0340-01", "name": "Westborough"},
        {"id": "WML-0364-01", "name": "Grafton"},
        {"id": "WML-0442-CS", "name": "Worcester"},
    ]

    assert MBTAClient._commuter_rail_service_detail(
        "CR-Worcester",
        0,
        trip_stops,
    ) == "Local"


@pytest.mark.asyncio
async def test_fetch_predictions_raw_uses_minimal_prediction_fields():
    client = MBTAClient(api_key="mock_key")
    mock_client = MockAsyncClient({"data": []})

    with patch("src.mbta_client.httpx.AsyncClient", return_value=mock_client):
        await client.fetch_predictions_raw(
            stop_id="place-andrw",
            route_id="Red",
            direction_id=0,
            page_limit=4,
        )

    mock_client.get.assert_awaited_once()
    kwargs = mock_client.get.await_args.kwargs
    assert kwargs["params"] == {
        "filter[stop]": "place-andrw",
        "include": "trip,vehicle",
        "fields[prediction]": "arrival_time,departure_time,direction_id,schedule_relationship,status,route,stop,trip,vehicle",
        "fields[trip]": "headsign,name",
        "fields[vehicle]": "current_status,stop",
        "sort": "time",
        "filter[route]": "Red",
        "filter[direction_id]": 0,
        "page[limit]": 4,
    }


@pytest.mark.asyncio
async def test_fetch_schedules_raw_uses_minimal_commuter_rail_fields():
    client = MBTAClient(api_key="mock_key")
    mock_client = MockAsyncClient({"data": []})

    with patch("src.mbta_client.httpx.AsyncClient", return_value=mock_client):
        with patch.object(
            MBTAClient,
            "_get_schedule_time_filters",
            return_value={"filter[date]": "2026-08-28", "filter[min_time]": "17:42"},
        ):
            await client.fetch_schedules_raw(
                stop_id="place-sstat",
                route_id="CR-Worcester",
                direction_id=0,
                page_limit=7,
            )

    mock_client.get.assert_awaited_once()
    kwargs = mock_client.get.await_args.kwargs
    assert kwargs["params"] == {
        "filter[stop]": "place-sstat",
        "include": "trip,prediction",
        "fields[schedule]": "arrival_time,departure_time,direction_id,prediction,trip",
        "fields[prediction]": "arrival_time,departure_time,direction_id,schedule_relationship,status",
        "fields[trip]": "headsign,name",
        "sort": "time",
        "filter[date]": "2026-08-28",
        "filter[min_time]": "17:42",
        "filter[route]": "CR-Worcester",
        "filter[direction_id]": 0,
        "page[limit]": 7,
    }


@pytest.mark.asyncio
async def test_fetch_trip_schedules_raw_uses_minimal_stop_fields():
    client = MBTAClient(api_key="mock_key")
    mock_client = MockAsyncClient({"data": []})

    with patch("src.mbta_client.httpx.AsyncClient", return_value=mock_client):
        await client.fetch_trip_schedules_raw(["trip_1", "trip_2", "trip_3"])

    mock_client.get.assert_awaited_once()
    kwargs = mock_client.get.await_args.kwargs
    assert kwargs["params"] == {
        "filter[trip]": "trip_1,trip_2,trip_3",
        "include": "stop",
        "fields[schedule]": "stop,trip",
        "fields[stop]": "name",
        "sort": "stop_sequence",
    }


@pytest.mark.asyncio
async def test_fetch_alerts_raw_uses_minimal_alert_fields():
    client = MBTAClient(api_key="mock_key")
    mock_client = MockAsyncClient({"data": []})

    with patch("src.mbta_client.httpx.AsyncClient", return_value=mock_client):
        await client.fetch_alerts_raw(
            stop_id="place-andrw",
            route_id="Red",
        )

    mock_client.get.assert_awaited_once()
    kwargs = mock_client.get.await_args.kwargs
    assert kwargs["params"] == {
        "filter[datetime]": "NOW",
        "fields[alert]": "header,short_header",
        "filter[stop]": "place-andrw",
        "filter[route]": "Red",
    }


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


@pytest.mark.asyncio
async def test_commuter_rail_uses_schedules_and_overlays_predictions():
    client = MBTAClient(api_key="mock_key")
    now = datetime.now(timezone.utc)
    scheduled_1 = now + timedelta(minutes=10)
    predicted_1 = now + timedelta(minutes=8)
    scheduled_2 = now + timedelta(minutes=20)
    scheduled_3 = now + timedelta(minutes=30)

    mock_schedule_data = {
        "data": [
            {
                "id": "schedule_1",
                "attributes": {
                    "departure_time": scheduled_1.isoformat(),
                    "direction_id": 0,
                    "stop_sequence": 1,
                },
                "relationships": {
                    "prediction": {"data": {"id": "prediction_1"}},
                    "route": {"data": {"id": "CR-Worcester"}},
                    "stop": {"data": {"id": "place-sstat"}},
                    "trip": {"data": {"id": "trip_1"}},
                },
            },
            {
                "id": "schedule_2",
                "attributes": {
                    "departure_time": scheduled_2.isoformat(),
                    "direction_id": 0,
                    "stop_sequence": 1,
                },
                "relationships": {
                    "route": {"data": {"id": "CR-Worcester"}},
                    "stop": {"data": {"id": "place-sstat"}},
                    "trip": {"data": {"id": "trip_2"}},
                },
            },
            {
                "id": "schedule_3",
                "attributes": {
                    "departure_time": scheduled_3.isoformat(),
                    "direction_id": 0,
                    "stop_sequence": 1,
                },
                "relationships": {
                    "route": {"data": {"id": "CR-Worcester"}},
                    "stop": {"data": {"id": "place-sstat"}},
                    "trip": {"data": {"id": "trip_3"}},
                },
            },
        ],
        "included": [
            {"type": "trip", "id": "trip_1", "attributes": {"headsign": "Worcester", "name": "P501"}},
            {"type": "trip", "id": "trip_2", "attributes": {"headsign": "Framingham", "name": "P503"}},
            {"type": "trip", "id": "trip_3", "attributes": {"headsign": "Worcester", "name": "P505"}},
            {
                "type": "prediction",
                "id": "prediction_1",
                "attributes": {
                    "departure_time": predicted_1.isoformat(),
                    "direction_id": 0,
                    "schedule_relationship": None,
                    "status": None,
                },
                "relationships": {
                    "route": {"data": {"id": "CR-Worcester"}},
                    "stop": {"data": {"id": "place-sstat"}},
                    "trip": {"data": {"id": "trip_1"}},
                },
            },
        ],
    }

    with patch.object(client, "fetch_schedules_raw", new_callable=AsyncMock) as mock_fetch_schedules:
        with patch.object(client, "fetch_predictions_raw", new_callable=AsyncMock) as mock_fetch_predictions:
            with patch.object(client, "fetch_alerts_raw", new_callable=AsyncMock) as mock_fetch_alerts:
                with patch.object(client, "fetch_trip_schedules_raw", new_callable=AsyncMock) as mock_fetch_trip_schedules:
                    mock_fetch_schedules.return_value = mock_schedule_data
                    mock_fetch_alerts.return_value = {"data": []}
                    mock_fetch_trip_schedules.return_value = {
                        "data": [
                            {"relationships": {"trip": {"data": {"id": "trip_1"}}, "stop": {"data": {"id": "NEC-2287"}}}},
                            {"relationships": {"trip": {"data": {"id": "trip_1"}}, "stop": {"data": {"id": "WML-0012-07"}}}},
                            {"relationships": {"trip": {"data": {"id": "trip_1"}}, "stop": {"data": {"id": "WML-0025-07"}}}},
                            {"relationships": {"trip": {"data": {"id": "trip_1"}}, "stop": {"data": {"id": "WML-0035-01"}}}},
                            {"relationships": {"trip": {"data": {"id": "trip_1"}}, "stop": {"data": {"id": "WML-0199-02"}}}},
                            {"relationships": {"trip": {"data": {"id": "trip_1"}}, "stop": {"data": {"id": "WML-0214-02"}}}},
                        ],
                        "included": [
                            {"type": "stop", "id": "NEC-2287", "attributes": {"name": "South Station"}},
                            {"type": "stop", "id": "WML-0012-07", "attributes": {"name": "Back Bay"}},
                            {"type": "stop", "id": "WML-0025-07", "attributes": {"name": "Lansdowne"}},
                            {"type": "stop", "id": "WML-0035-01", "attributes": {"name": "Boston Landing"}},
                            {"type": "stop", "id": "WML-0199-02", "attributes": {"name": "West Natick"}},
                            {"type": "stop", "id": "WML-0214-02", "attributes": {"name": "Framingham"}},
                        ],
                    }

                    route = RouteConfig(
                        id="worcester_line",
                        type="commuter_rail",
                        route_id="CR-Worcester",
                        stop_id="place-sstat",
                        name="Framingham/Worcester Line",
                        directions=[DirectionConfig(direction_id=0, headsign="Framingham / Worcester (Outbound)")],
                    )

                    result = await client.get_route_departures(route)

    departures = result["directions"][0]["departures"]
    assert result["error"] is None
    assert len(departures) == 3
    assert departures[0]["is_predicted"] is True
    assert departures[0]["time_source"] == "prediction"
    assert departures[0]["time_iso"] == predicted_1.isoformat()
    assert departures[0]["scheduled_time_iso"] == scheduled_1.isoformat()
    assert departures[1]["is_predicted"] is False
    assert departures[1]["time_source"] == "schedule"
    assert departures[1]["time_iso"] == scheduled_2.isoformat()
    assert departures[0]["service_detail"] == "Express to West Natick after Boston Landing"
    assert "_trip_id" not in departures[0]
    mock_fetch_predictions.assert_not_awaited()
    mock_fetch_trip_schedules.assert_awaited_once_with(["trip_1", "trip_2", "trip_3"])
    mock_fetch_schedules.assert_awaited_once_with(
        stop_id="place-sstat",
        route_id="CR-Worcester",
        route_filter=[],
        direction_id=0,
        page_limit=7,
    )


@pytest.mark.asyncio
async def test_subway_does_not_fetch_schedules():
    client = MBTAClient(api_key="mock_key")

    with patch.object(client, "fetch_schedules_raw", new_callable=AsyncMock) as mock_fetch_schedules:
        with patch.object(client, "fetch_predictions_raw", new_callable=AsyncMock) as mock_fetch_predictions:
            with patch.object(client, "fetch_alerts_raw", new_callable=AsyncMock) as mock_fetch_alerts:
                mock_fetch_predictions.return_value = {"data": [], "included": []}
                mock_fetch_alerts.return_value = {"data": []}

                route = RouteConfig(
                    id="red_line",
                    route_id="Red",
                    stop_id="place-andrw",
                    name="Red Line",
                    directions=[DirectionConfig(direction_id=0, headsign="Ashmont / Braintree")],
                )

                result = await client.get_route_departures(route)

    assert result["error"] is None
    mock_fetch_schedules.assert_not_awaited()
