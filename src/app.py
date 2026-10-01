from contextlib import asynccontextmanager
from datetime import datetime, timezone
import asyncio
import logging
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from src import __version__
from src.config import AppConfig, load_config
from src.mbta_client import MBTAClient
from src.nws_client import NWSClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("t-minus-pi")

# Paths
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"
STYLESHEET_PATH = STATIC_DIR / "css" / "styles.css"

STATIC_DIR.mkdir(parents=True, exist_ok=True)
TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# Global state
config: AppConfig = load_config()
mbta_client = MBTAClient(api_key=config.mbta_api_key)
nws_client: NWSClient | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global config, mbta_client, nws_client
    config = load_config()
    mbta_client = MBTAClient(api_key=config.mbta_api_key)
    nws_client = None
    if config.weather.enabled and config.nws_user_agent:
        nws_client = NWSClient(
            user_agent=config.nws_user_agent,
            weather_cache_ttl_seconds=config.weather.refresh_seconds,
        )
    logger.info("t-minus-pi backend initialized successfully.")
    logger.info(f"Loaded {len(config.routes)} configured route(s).")
    if config.mbta_api_key:
        logger.info("MBTA API key configured.")
    else:
        logger.warning("No MBTA API key provided in .env (running in unauthenticated mode).")
    if config.weather.enabled and not config.nws_user_agent:
        logger.warning("Weather is enabled but NWS_USER_AGENT is missing; weather fetches disabled.")
    try:
        yield
    finally:
        await mbta_client.aclose()
        if nws_client is not None:
            await nws_client.aclose()


app = FastAPI(
    title="t-minus-pi",
    version=__version__,
    description="Lightweight MBTA Real-time Transit Dashboard",
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/api/health")
async def health_check():
    """Healthcheck endpoint for monitoring and uptime probes."""
    return {
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "version": __version__,
        "routes_count": len(config.routes),
    }


@app.get("/api/config")
async def get_dashboard_config():
    """Return public display configuration."""
    return {
        "title": config.display.title,
        "clock_format_24h": config.display.clock_format_24h,
        "refresh_seconds": config.server.refresh_seconds,
        "show_alerts": config.display.show_alerts,
        "primary_text_scale": config.display.primary_text_scale,
    }


@app.get("/api/departures")
async def get_departures():
    """Fetch live departures and alerts for all configured routes."""
    departures_task = asyncio.create_task(
        mbta_client.get_all_departures(
            config.routes,
            max_per_direction=config.display.max_predictions_per_direction,
            include_alerts=config.display.show_alerts,
        )
    )
    weather_task = asyncio.create_task(_get_weather_payload())
    data, weather = await asyncio.gather(departures_task, weather_task)
    return {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "cards": data,
        "weather": weather,
    }


@app.get("/", response_class=HTMLResponse)
async def serve_dashboard(request: Request):
    """Render the dashboard UI."""
    stylesheet_version = int(STYLESHEET_PATH.stat().st_mtime)
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "title": config.display.title,
            "refresh_seconds": config.server.refresh_seconds,
            "primary_text_scale": config.display.primary_text_scale,
            "weather_text_scale": config.weather.text_scale,
            "stylesheet_version": stylesheet_version,
        },
    )


def _build_weather_unavailable_payload(message: str):
    label = config.weather.label or "Weather"
    return {
        "label": label,
        "available": False,
        "temperature_f": None,
        "condition": None,
        "humidity_percent": None,
        "wind_speed_mph": None,
        "wind_direction": None,
        "observed_at": None,
        "station_name": None,
        "error": message,
    }


async def _get_weather_payload():
    if not config.weather.enabled:
        return None
    if not config.nws_user_agent:
        return _build_weather_unavailable_payload("Set NWS_USER_AGENT to enable weather data.")
    if not config.weather.label:
        return _build_weather_unavailable_payload("Set weather.label to show current conditions.")
    if config.weather.latitude is None or config.weather.longitude is None:
        return _build_weather_unavailable_payload("Set weather.latitude and weather.longitude to show current conditions.")
    if nws_client is None:
        return _build_weather_unavailable_payload("Weather data is currently unavailable.")
    return await nws_client.get_current_weather(
        label=config.weather.label,
        latitude=config.weather.latitude,
        longitude=config.weather.longitude,
    )
