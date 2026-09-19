import pytest
from httpx import ASGITransport, AsyncClient
import src.app as app_module
from src.config import AppConfig, DisplayConfig


@pytest.mark.asyncio
async def test_get_root_dashboard(monkeypatch):
    monkeypatch.setattr(
        app_module,
        "config",
        AppConfig(display=DisplayConfig(primary_text_scale=1.25)),
    )
    transport = ASGITransport(app=app_module.app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")
    assert "<title>" in response.text
    assert '/static/css/styles.css?v=' in response.text
    assert "--primary-text-scale: 1.25;" in response.text


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
