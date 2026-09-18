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
        self.aclose = AsyncMock()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        pass


def make_prediction(
    prediction_id,
    direction_id,
    trip_id,
    arrival_time=None,
    departure_time=None,
    schedule_relationship=None,
    headsign="Alewife",
):
    return {
        "id": prediction_id,
        "attributes": {
            "arrival_time": arrival_time,
            "departure_time": departure_time,
            "direction_id": direction_id,
            "schedule_relationship": schedule_relationship,
            "status": None,
        },
        "relationships": {
            "route": {"data": {"id": "Red"}},
            "stop": {"data": {"id": "70084" if direction_id == 1 else "70083"}},
            "trip": {"data": {"id": trip_id}},
        },
    }, {
        "type": "trip",
        "id": trip_id,
        "attributes": {"headsign": headsign},
    }


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
async def test_fetch_predictions_raw_includes_uncertainty_fields_when_needed():
    client = MBTAClient(api_key="mock_key")
    mock_client = MockAsyncClient({"data": []})

    with patch("src.mbta_client.httpx.AsyncClient", return_value=mock_client):
        await client.fetch_predictions_raw(
            stop_id="place-andrw",
            route_id="10",
            direction_id=0,
            page_limit=9,
            include_uncertainty=True,
        )

    kwargs = mock_client.get.await_args.kwargs
    assert kwargs["params"]["fields[prediction]"] == (
        "arrival_time,departure_time,direction_id,schedule_relationship,status,"
        "route,stop,trip,vehicle,arrival_uncertainty,departure_uncertainty"
    )


@pytest.mark.asyncio
async def test_mbta_client_reuses_async_http_client_and_closes_it():
    mock_client = MockAsyncClient({"data": []})

    with patch("src.mbta_client.httpx.AsyncClient", return_value=mock_client) as client_factory:
        client = MBTAClient(api_key="mock_key")
        await client.fetch_predictions_raw(stop_id="place-andrw", route_id="Red")
        await client.fetch_alerts_raw(route_id="Red")
        await client.aclose()

    client_factory.assert_called_once_with(timeout=10.0)
    assert mock_client.get.await_count == 2
    mock_client.aclose.assert_awaited_once()


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


@pytest.mark.asyncio
async def test_get_route_departures_skips_alert_fetch_when_alerts_disabled():
    client = MBTAClient(api_key="mock_key")
    now = datetime.now(timezone.utc)
    prediction, trip = make_prediction(
        "prediction_1",
        1,
        "trip_1",
        arrival_time=(now + timedelta(minutes=4)).isoformat(),
    )

    with patch.object(client, "fetch_predictions_raw", new_callable=AsyncMock) as mock_fetch_pred:
        with patch.object(client, "fetch_alerts_cached", new_callable=AsyncMock) as mock_fetch_alerts:
            mock_fetch_pred.return_value = {"data": [prediction], "included": [trip]}
            route = RouteConfig(
                id="red_line",
                route_id="Red",
                stop_id="place-andrw",
                name="Andrew - Red Line",
                directions=[DirectionConfig(direction_id=1, headsign="Alewife")],
            )

            result = await client.get_route_departures(route, include_alerts=False)

    assert result["error"] is None
    assert result["alerts"] == []
    mock_fetch_alerts.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_route_departures_does_not_retry_when_first_page_has_enough_usable_predictions():
    client = MBTAClient(api_key="mock_key")
    now = datetime.now(timezone.utc)
    predictions = []
    included = []
    for index in range(3):
        prediction, trip = make_prediction(
            f"prediction_{index}",
            1,
            f"trip_{index}",
            arrival_time=(now + timedelta(minutes=index + 3)).isoformat(),
            departure_time=(now + timedelta(minutes=index + 3, seconds=10)).isoformat(),
        )
        predictions.append(prediction)
        included.append(trip)

    mock_pred_data = {"data": predictions, "included": included}

    with patch.object(client, "fetch_predictions_raw", new_callable=AsyncMock) as mock_fetch_pred:
        with patch.object(client, "fetch_alerts_raw", new_callable=AsyncMock) as mock_fetch_alerts:
            mock_fetch_pred.return_value = mock_pred_data
            mock_fetch_alerts.return_value = {"data": []}

            route = RouteConfig(
                id="red_line",
                route_id="Red",
                stop_id="place-andrw",
                name="Andrew - Red Line",
                directions=[DirectionConfig(direction_id=1, headsign="Alewife")],
            )

            result = await client.get_route_departures(route)

    assert result["error"] is None
    assert len(result["directions"][0]["departures"]) == 3
    mock_fetch_pred.assert_awaited_once_with(
        stop_id="place-andrw",
        route_id="Red",
        route_filter=[],
        direction_id=1,
        page_limit=4,
    )


@pytest.mark.asyncio
async def test_get_route_departures_retries_without_page_limit_when_first_page_is_crowded_by_unviable_predictions():
    client = MBTAClient(api_key="mock_key")
    now = datetime.now(timezone.utc)
    first_page_predictions = []
    first_page_included = []
    for index in range(3):
        prediction, trip = make_prediction(
            f"cancelled_{index}",
            1,
            f"cancelled_trip_{index}",
            schedule_relationship="CANCELLED",
        )
        first_page_predictions.append(prediction)
        first_page_included.append(trip)
    usable_prediction, usable_trip = make_prediction(
        "usable_first_page",
        1,
        "usable_first_page_trip",
        arrival_time=(now + timedelta(minutes=3)).isoformat(),
        departure_time=(now + timedelta(minutes=3, seconds=10)).isoformat(),
    )
    first_page_predictions.append(usable_prediction)
    first_page_included.append(usable_trip)

    retry_predictions = []
    retry_included = []
    for index in range(3):
        prediction, trip = make_prediction(
            f"usable_retry_{index}",
            1,
            f"usable_retry_trip_{index}",
            arrival_time=(now + timedelta(minutes=index + 4)).isoformat(),
            departure_time=(now + timedelta(minutes=index + 4, seconds=10)).isoformat(),
        )
        retry_predictions.append(prediction)
        retry_included.append(trip)

    with patch.object(client, "fetch_predictions_raw", new_callable=AsyncMock) as mock_fetch_pred:
        with patch.object(client, "fetch_alerts_raw", new_callable=AsyncMock) as mock_fetch_alerts:
            mock_fetch_pred.side_effect = [
                {"data": first_page_predictions, "included": first_page_included},
                {"data": retry_predictions, "included": retry_included},
            ]
            mock_fetch_alerts.return_value = {"data": []}

            route = RouteConfig(
                id="red_line",
                route_id="Red",
                stop_id="place-andrw",
                name="Andrew - Red Line",
                directions=[DirectionConfig(direction_id=1, headsign="Alewife")],
            )

            result = await client.get_route_departures(route)

    assert result["error"] is None
    assert len(result["directions"][0]["departures"]) == 3
    assert [
        departure["time_iso"]
        for departure in result["directions"][0]["departures"]
    ] == [
        prediction["attributes"]["arrival_time"]
        for prediction in retry_predictions
    ]
    mock_fetch_pred.assert_has_awaits([
        call(
            stop_id="place-andrw",
            route_id="Red",
            route_filter=[],
            direction_id=1,
            page_limit=4,
        ),
        call(
            stop_id="place-andrw",
            route_id="Red",
            route_filter=[],
            direction_id=1,
            page_limit=None,
        ),
    ])


@pytest.mark.asyncio
async def test_get_route_departures_does_not_retry_when_short_first_page_has_unviable_predictions():
    client = MBTAClient(api_key="mock_key")
    now = datetime.now(timezone.utc)
    cancelled_prediction, cancelled_trip = make_prediction(
        "cancelled",
        1,
        "cancelled_trip",
        schedule_relationship="CANCELLED",
    )
    usable_prediction, usable_trip = make_prediction(
        "usable",
        1,
        "usable_trip",
        arrival_time=(now + timedelta(minutes=3)).isoformat(),
        departure_time=(now + timedelta(minutes=3, seconds=10)).isoformat(),
    )
    mock_pred_data = {
        "data": [cancelled_prediction, usable_prediction],
        "included": [cancelled_trip, usable_trip],
    }

    with patch.object(client, "fetch_predictions_raw", new_callable=AsyncMock) as mock_fetch_pred:
        with patch.object(client, "fetch_alerts_raw", new_callable=AsyncMock) as mock_fetch_alerts:
            mock_fetch_pred.return_value = mock_pred_data
            mock_fetch_alerts.return_value = {"data": []}

            route = RouteConfig(
                id="red_line",
                route_id="Red",
                stop_id="place-andrw",
                name="Andrew - Red Line",
                directions=[DirectionConfig(direction_id=1, headsign="Alewife")],
            )

            result = await client.get_route_departures(route)

    assert result["error"] is None
    assert len(result["directions"][0]["departures"]) == 1
    mock_fetch_pred.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_route_departures_remains_empty_when_unbounded_retry_has_no_usable_predictions():
    client = MBTAClient(api_key="mock_key")
    first_page_predictions = []
    first_page_included = []
    retry_predictions = []
    retry_included = []
    for index in range(4):
        prediction, trip = make_prediction(
            f"cancelled_{index}",
            1,
            f"cancelled_trip_{index}",
            schedule_relationship="CANCELLED",
        )
        first_page_predictions.append(prediction)
        first_page_included.append(trip)
    for index in range(2):
        prediction, trip = make_prediction(
            f"retry_cancelled_{index}",
            1,
            f"retry_cancelled_trip_{index}",
            schedule_relationship="CANCELLED",
        )
        retry_predictions.append(prediction)
        retry_included.append(trip)

    with patch.object(client, "fetch_predictions_raw", new_callable=AsyncMock) as mock_fetch_pred:
        with patch.object(client, "fetch_alerts_raw", new_callable=AsyncMock) as mock_fetch_alerts:
            mock_fetch_pred.side_effect = [
                {"data": first_page_predictions, "included": first_page_included},
                {"data": retry_predictions, "included": retry_included},
            ]
            mock_fetch_alerts.return_value = {"data": []}

            route = RouteConfig(
                id="red_line",
                route_id="Red",
                stop_id="place-andrw",
                name="Andrew - Red Line",
                directions=[DirectionConfig(direction_id=1, headsign="Alewife")],
            )

            result = await client.get_route_departures(route)

    assert result["error"] is None
    assert result["directions"][0]["departures"] == []
    assert mock_fetch_pred.await_args_list[-1] == call(
        stop_id="place-andrw",
        route_id="Red",
        route_filter=[],
        direction_id=1,
        page_limit=None,
    )


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


def test_has_live_bus_time_filters_schedule_only_predictions():
    assert MBTAClient._has_live_bus_time({
        "attributes": {
            "arrival_time": "2026-08-31T15:00:00-04:00",
            "arrival_uncertainty": None,
        }
    })
    assert MBTAClient._has_live_bus_time({
        "attributes": {
            "departure_time": "2026-08-31T15:00:00-04:00",
            "departure_uncertainty": 301,
        }
    })
    assert not MBTAClient._has_live_bus_time({
        "attributes": {
            "arrival_time": "2026-08-31T15:00:00-04:00",
            "arrival_uncertainty": 300,
        }
    })
    assert not MBTAClient._has_live_bus_time({
        "attributes": {
            "departure_time": "2026-08-31T15:00:00-04:00",
            "departure_uncertainty": 302,
        }
    })


def test_alert_filter_kwargs_prefers_selected_routes_over_stops():
    assert MBTAClient._alert_filter_kwargs_for_route(RouteConfig(
        id="worcester_line",
        type="commuter_rail",
        route_id="CR-Worcester",
        stop_id="place-sstat",
        name="Framingham/Worcester Line",
    )) == {"route_id": "CR-Worcester"}

    assert MBTAClient._alert_filter_kwargs_for_route(RouteConfig(
        id="local_buses",
        type="bus",
        stop_id="place-andrw",
        name="Local Buses",
        route_filter=["10", "708"],
    )) == {"route_filter": ["10", "708"]}

    assert MBTAClient._alert_filter_kwargs_for_route(RouteConfig(
        id="stop_only",
        stop_id="place-andrw",
        name="Andrew",
    )) == {"stop_id": "place-andrw"}


@pytest.mark.asyncio
async def test_bus_targets_use_exact_route_stop_direction_and_hide_non_live_predictions():
    client = MBTAClient(api_key="mock_key")
    now = datetime.now(timezone.utc)
    schedule_only_time = now + timedelta(minutes=3)
    live_time = now + timedelta(minutes=8)
    second_live_time = now + timedelta(minutes=12)

    outbound_data = {
        "data": [
            {
                "id": "schedule_only",
                "attributes": {
                    "arrival_time": schedule_only_time.isoformat(),
                    "arrival_uncertainty": 300,
                    "departure_time": schedule_only_time.isoformat(),
                    "departure_uncertainty": 300,
                    "direction_id": 0,
                },
                "relationships": {
                    "route": {"data": {"id": "10"}},
                    "stop": {"data": {"id": "place-andrw"}},
                    "trip": {"data": {"id": "trip_schedule"}},
                },
            },
            {
                "id": "live",
                "attributes": {
                    "arrival_time": live_time.isoformat(),
                    "arrival_uncertainty": None,
                    "departure_time": live_time.isoformat(),
                    "departure_uncertainty": None,
                    "direction_id": 0,
                },
                "relationships": {
                    "route": {"data": {"id": "10"}},
                    "stop": {"data": {"id": "place-andrw"}},
                    "trip": {"data": {"id": "trip_live"}},
                },
            },
            {
                "id": "second_live",
                "attributes": {
                    "arrival_time": second_live_time.isoformat(),
                    "arrival_uncertainty": None,
                    "departure_time": second_live_time.isoformat(),
                    "departure_uncertainty": None,
                    "direction_id": 0,
                },
                "relationships": {
                    "route": {"data": {"id": "10"}},
                    "stop": {"data": {"id": "place-andrw"}},
                    "trip": {"data": {"id": "trip_second_live"}},
                },
            },
        ],
        "included": [
            {"type": "route", "id": "10", "attributes": {"short_name": "10", "color": "FFC72C"}},
            {"type": "trip", "id": "trip_live", "attributes": {"headsign": "City Point"}},
            {"type": "trip", "id": "trip_second_live", "attributes": {"headsign": "City Point"}},
            {"type": "trip", "id": "trip_schedule", "attributes": {"headsign": "City Point"}},
        ],
    }
    inbound_data = {"data": [], "included": []}

    with patch.object(client, "fetch_predictions_raw", new_callable=AsyncMock) as mock_fetch_pred:
        with patch.object(client, "fetch_alerts_cached", new_callable=AsyncMock) as mock_fetch_alerts:
            mock_fetch_pred.side_effect = [outbound_data, inbound_data]
            mock_fetch_alerts.return_value = {"data": []}

            route = RouteConfig(
                id="local_buses",
                type="bus",
                stop_id="place-andrw",
                name="Local Buses",
                bus_targets=[
                    {
                        "route_id": "10",
                        "route_name": "10",
                        "stop_id": "place-andrw",
                        "stop_name": "Andrew",
                        "direction_id": 0,
                        "direction_name": "Outbound",
                    },
                    {
                        "route_id": "10",
                        "route_name": "10",
                        "stop_id": "place-andrw",
                        "stop_name": "Andrew",
                        "direction_id": 1,
                        "direction_name": "Inbound",
                    },
                ],
            )

            result = await client.get_route_departures(route)

    departures = result["directions"][0]["departures"]
    assert result["error"] is None
    assert result["directions"][0]["name"] == "Live ETA"
    assert len(departures) == 2
    assert departures[0]["route_name"] == "10"
    assert departures[0]["headsign"] == "City Point"
    assert departures[0]["direction_label"] == "Outbound"
    assert departures[0]["stop_name"] == "Andrew"
    assert departures[0]["time_iso"] == live_time.isoformat()
    assert departures[1]["time_iso"] == second_live_time.isoformat()
    mock_fetch_pred.assert_has_awaits([
        call(stop_id="place-andrw", route_id="10", direction_id=0, page_limit=9, include_uncertainty=True),
        call(stop_id="place-andrw", route_id="10", direction_id=1, page_limit=9, include_uncertainty=True),
    ])
    mock_fetch_alerts.assert_awaited_once_with(route_filter=["10"])


@pytest.mark.asyncio
async def test_bus_targets_query_ct3_by_route_id_708_and_display_short_name():
    client = MBTAClient(api_key="mock_key")
    live_time = datetime.now(timezone.utc) + timedelta(minutes=12)

    mock_pred_data = {
        "data": [
            {
                "id": "ct3_live",
                "attributes": {
                    "departure_time": live_time.isoformat(),
                    "departure_uncertainty": None,
                    "direction_id": 0,
                },
                "relationships": {
                    "route": {"data": {"id": "708"}},
                    "stop": {"data": {"id": "place-andrw"}},
                    "trip": {"data": {"id": "trip_ct3"}},
                },
            }
        ],
        "included": [
            {"type": "route", "id": "708", "attributes": {"short_name": "CT3", "color": "FFC72C"}},
            {"type": "trip", "id": "trip_ct3", "attributes": {"headsign": "Avenue Louis Pasteur"}},
        ],
    }

    with patch.object(client, "fetch_predictions_raw", new_callable=AsyncMock) as mock_fetch_pred:
        with patch.object(client, "fetch_alerts_cached", new_callable=AsyncMock) as mock_fetch_alerts:
            mock_fetch_pred.return_value = mock_pred_data
            mock_fetch_alerts.return_value = {"data": []}

            route = RouteConfig(
                id="local_buses",
                type="bus",
                stop_id="place-andrw",
                name="Local Buses",
                bus_targets=[
                    {
                        "route_id": "708",
                        "route_name": "CT3",
                        "stop_id": "place-andrw",
                        "stop_name": "Andrew",
                        "direction_id": 0,
                        "direction_name": "Outbound",
                    }
                ],
            )

            result = await client.get_route_departures(route)

    departure = result["directions"][0]["departures"][0]
    assert departure["route_id"] == "708"
    assert departure["route_name"] == "CT3"
    mock_fetch_pred.assert_awaited_once_with(
        stop_id="place-andrw",
        route_id="708",
        direction_id=0,
        page_limit=9,
        include_uncertainty=True,
    )
    mock_fetch_alerts.assert_awaited_once_with(route_filter=["708"])


@pytest.mark.asyncio
async def test_bus_targets_show_next_eight_predictions_sorted_by_eta():
    client = MBTAClient(api_key="mock_key")
    now = datetime.now(timezone.utc)
    prediction_times = [
        now + timedelta(minutes=minutes)
        for minutes in [9, 1, 4, 8, 2, 7, 3, 6, 5]
    ]

    mock_pred_data = {
        "data": [
            {
                "id": f"prediction_{index}",
                "attributes": {
                    "arrival_time": prediction_time.isoformat(),
                    "arrival_uncertainty": None,
                    "direction_id": 0,
                },
                "relationships": {
                    "route": {"data": {"id": "10"}},
                    "stop": {"data": {"id": "place-andrw"}},
                    "trip": {"data": {"id": f"trip_{index}"}},
                },
            }
            for index, prediction_time in enumerate(prediction_times)
        ],
        "included": [
            {"type": "route", "id": "10", "attributes": {"short_name": "10", "color": "FFC72C"}},
            *[
                {"type": "trip", "id": f"trip_{index}", "attributes": {"headsign": "City Point"}}
                for index in range(len(prediction_times))
            ],
        ],
    }

    with patch.object(client, "fetch_predictions_raw", new_callable=AsyncMock) as mock_fetch_pred:
        with patch.object(client, "fetch_alerts_cached", new_callable=AsyncMock) as mock_fetch_alerts:
            mock_fetch_pred.return_value = mock_pred_data
            mock_fetch_alerts.return_value = {"data": []}

            route = RouteConfig(
                id="local_buses",
                type="bus",
                stop_id="place-andrw",
                name="Local Buses",
                bus_targets=[
                    {
                        "route_id": "10",
                        "route_name": "10",
                        "stop_id": "place-andrw",
                        "stop_name": "Andrew",
                        "direction_id": 0,
                        "direction_name": "Outbound",
                    }
                ],
            )

            result = await client.get_route_departures(route)

    departures = result["directions"][0]["departures"]
    assert len(departures) == 8
    assert [departure["time_iso"] for departure in departures] == [
        prediction_time.isoformat()
        for prediction_time in sorted(prediction_times)[:8]
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
