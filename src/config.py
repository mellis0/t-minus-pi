import os
import math
from pathlib import Path
from typing import List, Optional
import yaml
from dotenv import dotenv_values, load_dotenv
from pydantic import BaseModel, Field, field_validator


class DirectionConfig(BaseModel):
    direction_id: int
    headsign: Optional[str] = None


class BusTargetConfig(BaseModel):
    route_id: str
    stop_id: str
    direction_id: int
    route_name: Optional[str] = None
    direction_name: Optional[str] = None
    stop_name: Optional[str] = None


class RouteConfig(BaseModel):
    id: str
    type: str = "subway"  # subway, commuter_rail, bus
    route_id: Optional[str] = None
    stop_id: str
    name: str
    directions: List[DirectionConfig] = Field(default_factory=list)
    route_filter: List[str] = Field(default_factory=list)
    bus_targets: List[BusTargetConfig] = Field(default_factory=list)


class ServerConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8000
    refresh_seconds: int = 10


class DisplayConfig(BaseModel):
    title: str = "MBTA Transit Departures"
    clock_format_24h: bool = False
    max_predictions_per_direction: int = 3
    show_alerts: bool = True
    primary_text_scale: float = 1.0

    @field_validator("primary_text_scale", mode="before")
    @classmethod
    def clamp_primary_text_scale(cls, value):
        if value is None:
            return 1.0
        try:
            scale = float(value)
        except (TypeError, ValueError):
            return 1.0
        if not math.isfinite(scale):
            return 1.0
        return max(scale, 0.8)


class AppConfig(BaseModel):
    mbta_api_key: Optional[str] = None
    server: ServerConfig = Field(default_factory=ServerConfig)
    display: DisplayConfig = Field(default_factory=DisplayConfig)
    routes: List[RouteConfig] = Field(default_factory=list)


def load_config(
    config_path: str | Path = "config.yaml",
    env_path: str | Path = ".env",
) -> AppConfig:
    """Load configuration from .env and YAML file."""
    # Load .env file
    env_file = Path(env_path)
    env_values = {}
    if env_file.exists():
        env_values = {k: v for k, v in dotenv_values(env_file).items() if v is not None}
        load_dotenv(dotenv_path=env_file, override=True)
    else:
        load_dotenv(override=True)

    api_key = (
        os.getenv("MBTA_API_KEY", "").strip()
        or os.getenv("KEY", "").strip()
        or None
    )
    env_host = env_values.get("HOST") if env_values else os.getenv("HOST")
    env_port = env_values.get("PORT") if env_values else os.getenv("PORT")

    config_file = Path(config_path)
    raw_data = {}
    if config_file.exists():
        with open(config_file, "r", encoding="utf-8") as f:
            raw_data = yaml.safe_load(f) or {}

    server_data = raw_data.get("server", {})
    if env_host:
        server_data["host"] = env_host
    if env_port:
        try:
            server_data["port"] = int(env_port)
        except ValueError:
            pass

    display_data = raw_data.get("display", {})
    routes_data = raw_data.get("routes", [])

    return AppConfig(
        mbta_api_key=api_key,
        server=ServerConfig(**server_data),
        display=DisplayConfig(**display_data),
        routes=[RouteConfig(**r) for r in routes_data],
    )
