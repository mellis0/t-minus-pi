from contextlib import asynccontextmanager
from datetime import datetime, timezone
import logging
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from src import __version__
from src.config import AppConfig, load_config
from src.mbta_client import MBTAClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("t-minus-pi")

# Paths
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"

STATIC_DIR.mkdir(parents=True, exist_ok=True)
TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# Global state
config: AppConfig = load_config()
mbta_client = MBTAClient(api_key=config.mbta_api_key)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global config, mbta_client
    config = load_config()
    mbta_client = MBTAClient(api_key=config.mbta_api_key)
    logger.info("t-minus-pi backend initialized successfully.")
    logger.info(f"Loaded {len(config.routes)} configured route(s).")
    if config.mbta_api_key:
        logger.info("MBTA API key configured.")
    else:
        logger.warning("No MBTA API key provided in .env (running in unauthenticated mode).")
    yield


app = FastAPI(
    title="t-minus-pi",
    version=__version__,
    description="Lightweight MBTA Real-time Transit Dashboard",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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
        "routes": [
            {
                "id": r.id,
                "name": r.name,
                "type": r.type,
                "stop_id": r.stop_id,
                "route_id": r.route_id,
                "bus_targets": [
                    {
                        "route_id": target.route_id,
                        "stop_id": target.stop_id,
                        "direction_id": target.direction_id,
                        "route_name": target.route_name,
                        "direction_name": target.direction_name,
                        "stop_name": target.stop_name,
                    }
                    for target in r.bus_targets
                ],
            }
            for r in config.routes
        ],
    }


@app.get("/api/departures")
async def get_departures():
    """Fetch live departures and alerts for all configured routes."""
    data = await mbta_client.get_all_departures(
        config.routes,
        max_per_direction=config.display.max_predictions_per_direction,
    )
    return {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "cards": data,
    }


@app.get("/", response_class=HTMLResponse)
async def serve_dashboard(request: Request):
    """Render the dashboard UI."""
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "title": config.display.title,
            "refresh_seconds": config.server.refresh_seconds,
        },
    )
