from unittest.mock import AsyncMock, patch

import pytest

from src.nws_client import NWSClient


class MockResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


class MockAsyncClient:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.get = AsyncMock(side_effect=self._get)
        self.aclose = AsyncMock()

    async def _get(self, *args, **kwargs):
        return MockResponse(self.payloads.pop(0))


@pytest.mark.asyncio
async def test_get_current_weather_resolves_station_and_converts_units():
    mock_client = MockAsyncClient([
        {
            "features": [
                {
                    "properties": {
                        "stationIdentifier": "KBOS",
                    }
                }
            ]
        },
        {
            "stationName": "Boston, Logan International Airport",
            "timestamp": "2026-10-01T12:00:00+00:00",
            "textDescription": "Mostly Cloudy",
            "temperature": {"value": 16.0, "unitCode": "wmoUnit:degC"},
            "relativeHumidity": {"value": 54.2, "unitCode": "wmoUnit:percent"},
            "windSpeed": {"value": 5.0, "unitCode": "wmoUnit:m_s-1"},
            "windDirection": {"value": 315, "unitCode": "wmoUnit:degree_(angle)"},
        },
    ])

    with patch("src.nws_client.httpx.AsyncClient", return_value=mock_client):
        client = NWSClient(user_agent="t-minus-pi-test (test@example.com)")
        weather = await client.get_current_weather("Cambridge, MA", 42.3736, -71.1097)

    assert weather == {
        "label": "Cambridge, MA",
        "available": True,
        "temperature_f": 61,
        "condition": "Mostly Cloudy",
        "humidity_percent": 54,
        "wind_speed_mph": 11,
        "wind_direction": "NW",
        "observed_at": "2026-10-01T12:00:00+00:00",
        "station_name": "Boston, Logan International Airport",
        "error": None,
    }
    assert mock_client.get.await_count == 2
    first_call = mock_client.get.await_args_list[0]
    second_call = mock_client.get.await_args_list[1]
    assert first_call.kwargs["headers"]["User-Agent"] == "t-minus-pi-test (test@example.com)"
    assert first_call.args[0].endswith("/points/42.3736,-71.1097/stations")
    assert second_call.args[0].endswith("/stations/KBOS/observations/latest")
    assert second_call.kwargs["params"] == {"require_qc": "true"}


@pytest.mark.asyncio
async def test_get_current_weather_uses_station_and_weather_cache():
    mock_client = MockAsyncClient([
        {"features": [{"properties": {"stationIdentifier": "KBOS"}}]},
        {
            "stationName": "Boston, Logan International Airport",
            "timestamp": "2026-10-01T12:00:00+00:00",
            "textDescription": "Clear",
            "temperature": {"value": 20.0, "unitCode": "wmoUnit:degC"},
        },
    ])

    with patch("src.nws_client.httpx.AsyncClient", return_value=mock_client):
        client = NWSClient(
            user_agent="t-minus-pi-test (test@example.com)",
            weather_cache_ttl_seconds=600,
        )
        first = await client.get_current_weather("Cambridge, MA", 42.3736, -71.1097)
        second = await client.get_current_weather("Cambridge, MA", 42.3736, -71.1097)

    assert first == second
    assert mock_client.get.await_count == 2


@pytest.mark.asyncio
async def test_get_current_weather_returns_unavailable_without_station():
    mock_client = MockAsyncClient([
        {"features": []},
    ])

    with patch("src.nws_client.httpx.AsyncClient", return_value=mock_client):
        client = NWSClient(user_agent="t-minus-pi-test (test@example.com)")
        weather = await client.get_current_weather("Nowhere, USA", 35.0, -100.0)

    assert weather["available"] is False
    assert weather["error"] == "No nearby NWS observation station found."
    assert mock_client.get.await_count == 1


@pytest.mark.asyncio
async def test_nws_client_reuses_async_http_client_and_closes_it():
    mock_client = MockAsyncClient([
        {"features": [{"properties": {"stationIdentifier": "KBOS"}}]},
        {
            "stationName": "Boston, Logan International Airport",
            "timestamp": "2026-10-01T12:00:00+00:00",
            "textDescription": "Clear",
            "temperature": {"value": 20.0, "unitCode": "wmoUnit:degC"},
        },
    ])

    with patch("src.nws_client.httpx.AsyncClient", return_value=mock_client) as client_factory:
        client = NWSClient(user_agent="t-minus-pi-test (test@example.com)")
        await client.get_current_weather("Cambridge, MA", 42.3736, -71.1097)
        await client.aclose()

    client_factory.assert_called_once_with(timeout=10.0, follow_redirects=True)
    mock_client.aclose.assert_awaited_once()


def test_parse_current_weather_returns_unavailable_when_observation_is_empty():
    payload = {
        "stationName": "Boston, Logan International Airport",
    }

    weather = NWSClient._parse_current_weather("Cambridge, MA", "KBOS", payload)

    assert weather["available"] is False
    assert weather["error"] == "No current observation data available."
