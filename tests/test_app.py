import pytest
from unittest.mock import AsyncMock
from httpx import ASGITransport, AsyncClient
import src.app as app_module
from src.config import AppConfig, DisplayConfig, WeatherConfig


@pytest.mark.asyncio
async def test_get_root_dashboard(monkeypatch):
    monkeypatch.setattr(
        app_module,
        "config",
        AppConfig(
            display=DisplayConfig(primary_text_scale=1.25),
            weather=WeatherConfig(text_scale=1.4),
        ),
    )
    transport = ASGITransport(app=app_module.app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")
    assert "<title>" in response.text
    assert '/static/css/styles.css?v=' in response.text
    assert "--primary-text-scale: 1.25;" in response.text
    assert "--weather-text-scale: 1.4;" in response.text


@pytest.mark.asyncio
async def test_get_health():
    transport = ASGITransport(app=app_module.app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"


@pytest.mark.asyncio
async def test_get_config(monkeypatch):
    monkeypatch.setattr(
        app_module,
        "config",
        AppConfig(display=DisplayConfig(primary_text_scale=1.25)),
    )
    transport = ASGITransport(app=app_module.app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get("/api/config")
    assert response.status_code == 200
    data = response.json()
    assert "title" in data
    assert "refresh_seconds" in data
    assert data["primary_text_scale"] == 1.25
    assert "routes" not in data


@pytest.mark.asyncio
async def test_get_departures_returns_null_weather_when_disabled(monkeypatch):
    monkeypatch.setattr(app_module, "config", AppConfig())
    monkeypatch.setattr(app_module.mbta_client, "get_all_departures", AsyncMock(return_value=[]))

    transport = ASGITransport(app=app_module.app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get("/api/departures")

    assert response.status_code == 200
    data = response.json()
    assert data["cards"] == []
    assert data["weather"] is None


@pytest.mark.asyncio
async def test_get_departures_returns_weather_payload_when_enabled(monkeypatch):
    weather = WeatherConfig(
        enabled=True,
        label="Cambridge, MA",
        latitude=42.3736,
        longitude=-71.1097,
        refresh_seconds=600,
    )
    monkeypatch.setattr(
        app_module,
        "config",
        AppConfig(
            nws_user_agent="t-minus-pi-test (test@example.com)",
            weather=weather,
        ),
    )
    monkeypatch.setattr(app_module.mbta_client, "get_all_departures", AsyncMock(return_value=[]))

    class MockNWSClient:
        async def get_current_weather(self, label, latitude, longitude):
            return {
                "label": label,
                "available": True,
                "temperature_f": 61,
                "condition": "Mostly Cloudy",
                "humidity_percent": 54,
                "wind_speed_mph": 8,
                "wind_direction": "NW",
                "observed_at": "2026-10-01T12:00:00+00:00",
                "station_name": "Boston, Logan International Airport",
                "error": None,
            }

    monkeypatch.setattr(app_module, "nws_client", MockNWSClient())

    transport = ASGITransport(app=app_module.app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get("/api/departures")

    assert response.status_code == 200
    data = response.json()
    assert data["weather"]["available"] is True
    assert data["weather"]["temperature_f"] == 61
    assert data["weather"]["label"] == "Cambridge, MA"


@pytest.mark.asyncio
async def test_get_departures_returns_weather_error_when_user_agent_missing(monkeypatch):
    monkeypatch.setattr(
        app_module,
        "config",
        AppConfig(
            weather=WeatherConfig(
                enabled=True,
                label="Cambridge, MA",
                latitude=42.3736,
                longitude=-71.1097,
            ),
        ),
    )
    monkeypatch.setattr(app_module.mbta_client, "get_all_departures", AsyncMock(return_value=[]))
    monkeypatch.setattr(app_module, "nws_client", None)

    transport = ASGITransport(app=app_module.app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get("/api/departures")

    assert response.status_code == 200
    data = response.json()
    assert data["weather"]["available"] is False
    assert "NWS_USER_AGENT" in data["weather"]["error"]
