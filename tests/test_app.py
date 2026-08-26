import pytest
from httpx import ASGITransport, AsyncClient
from src.app import app


@pytest.mark.asyncio
async def test_get_root_dashboard():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")
    assert "<title>" in response.text


@pytest.mark.asyncio
async def test_get_health():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"


@pytest.mark.asyncio
async def test_get_config():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get("/api/config")
    assert response.status_code == 200
    data = response.json()
    assert "title" in data
    assert "refresh_seconds" in data
