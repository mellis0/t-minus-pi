import logging
from time import monotonic
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger(__name__)

NWS_API_BASE_URL = "https://api.weather.gov"


class NWSClient:
    """Client for fetching and normalizing current weather from weather.gov."""

    def __init__(
        self,
        user_agent: str,
        base_url: str = NWS_API_BASE_URL,
        weather_cache_ttl_seconds: float = 600.0,
        http_client: Optional[httpx.AsyncClient] = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.weather_cache_ttl_seconds = weather_cache_ttl_seconds
        self._http_client = http_client
        self._owns_http_client = http_client is None
        self._station_cache: Dict[tuple[float, float], str] = {}
        self._weather_cache: Dict[tuple[str, float, float], tuple[float, Dict[str, Any]]] = {}
        self.headers = {
            "Accept": "application/ld+json",
            "User-Agent": user_agent,
        }

    def _get_http_client(self) -> httpx.AsyncClient:
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(timeout=10.0, follow_redirects=True)
        return self._http_client

    async def aclose(self) -> None:
        if self._http_client is not None and self._owns_http_client:
            await self._http_client.aclose()
            self._http_client = None

    async def _get_json(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        response = await self._get_http_client().get(
            f"{self.base_url}{path}",
            params=params,
            headers=self.headers,
        )
        response.raise_for_status()
        return response.json()

    @staticmethod
    def _extract_properties(payload: Dict[str, Any]) -> Dict[str, Any]:
        properties = payload.get("properties")
        return properties if isinstance(properties, dict) else payload

    @staticmethod
    def _normalize_unit_code(unit_code: Optional[str]) -> str:
        return (unit_code or "").strip().lower()

    @classmethod
    def _to_fahrenheit(cls, value: Optional[float], unit_code: Optional[str]) -> Optional[int]:
        if value is None:
            return None

        normalized = cls._normalize_unit_code(unit_code)
        if "degf" in normalized:
            return round(value)
        if "degc" in normalized or normalized.endswith(":c") or normalized == "c":
            return round((value * 9 / 5) + 32)
        return round(value)

    @classmethod
    def _to_mph(cls, value: Optional[float], unit_code: Optional[str]) -> Optional[int]:
        if value is None:
            return None

        normalized = cls._normalize_unit_code(unit_code)
        if "mi_h-1" in normalized or "mph" in normalized:
            return round(value)
        if "km_h-1" in normalized or "km/h" in normalized:
            return round(value * 0.621371)
        if "m_s-1" in normalized or "m/s" in normalized:
            return round(value * 2.23694)
        if normalized.endswith(":kt") or normalized == "kt":
            return round(value * 1.15078)
        return round(value)

    @staticmethod
    def _to_percent(value: Optional[float]) -> Optional[int]:
        if value is None:
            return None
        return round(value)

    @staticmethod
    def _degrees_to_compass(value: Optional[float]) -> Optional[str]:
        if value is None:
            return None
        directions = [
            "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
            "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
        ]
        index = round(value / 22.5) % len(directions)
        return directions[index]

    @staticmethod
    def _quantitative_value(payload: Dict[str, Any], field: str) -> tuple[Optional[float], Optional[str]]:
        raw = payload.get(field)
        if not isinstance(raw, dict):
            return None, None
        value = raw.get("value")
        if value is None:
            return None, raw.get("unitCode")
        try:
            return float(value), raw.get("unitCode")
        except (TypeError, ValueError):
            return None, raw.get("unitCode")

    @classmethod
    def _parse_station_id(cls, payload: Dict[str, Any]) -> Optional[str]:
        features = payload.get("features")
        if isinstance(features, list):
            for feature in features:
                props = cls._extract_properties(feature if isinstance(feature, dict) else {})
                station_id = props.get("stationIdentifier") or props.get("stationId")
                if station_id:
                    return str(station_id)

        station_urls = payload.get("observationStations")
        if isinstance(station_urls, list) and station_urls:
            first = station_urls[0]
            if isinstance(first, str) and first.strip():
                return first.rstrip("/").rsplit("/", 1)[-1]

        return None

    @classmethod
    def _build_unavailable_payload(cls, label: str, error: str) -> Dict[str, Any]:
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
            "error": error,
        }

    @classmethod
    def _parse_current_weather(
        cls,
        label: str,
        station_id: str,
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        data = cls._extract_properties(payload)

        temperature_value, temperature_unit = cls._quantitative_value(data, "temperature")
        humidity_value, _ = cls._quantitative_value(data, "relativeHumidity")
        wind_speed_value, wind_speed_unit = cls._quantitative_value(data, "windSpeed")
        wind_direction_value, _ = cls._quantitative_value(data, "windDirection")

        temperature_f = cls._to_fahrenheit(temperature_value, temperature_unit)
        humidity_percent = cls._to_percent(humidity_value)
        wind_speed_mph = cls._to_mph(wind_speed_value, wind_speed_unit)
        wind_direction = cls._degrees_to_compass(wind_direction_value)
        condition = data.get("textDescription") or None
        observed_at = data.get("timestamp") or None
        station_name = data.get("stationName") or station_id

        if all(
            value is None
            for value in (temperature_f, condition, humidity_percent, wind_speed_mph, observed_at)
        ):
            return cls._build_unavailable_payload(label, "No current observation data available.")

        return {
            "label": label,
            "available": True,
            "temperature_f": temperature_f,
            "condition": condition,
            "humidity_percent": humidity_percent,
            "wind_speed_mph": wind_speed_mph,
            "wind_direction": wind_direction,
            "observed_at": observed_at,
            "station_name": station_name,
            "error": None,
        }

    async def get_station_id(self, latitude: float, longitude: float) -> Optional[str]:
        key = (latitude, longitude)
        cached = self._station_cache.get(key)
        if cached:
            return cached

        payload = await self._get_json(f"/points/{latitude},{longitude}/stations")
        station_id = self._parse_station_id(payload)
        if station_id:
            self._station_cache[key] = station_id
        return station_id

    async def get_current_weather(self, label: str, latitude: float, longitude: float) -> Dict[str, Any]:
        cache_key = (label, latitude, longitude)
        now = monotonic()
        cached = self._weather_cache.get(cache_key)
        if cached:
            fetched_at, payload = cached
            if now - fetched_at < self.weather_cache_ttl_seconds:
                return payload

        try:
            station_id = await self.get_station_id(latitude, longitude)
            if not station_id:
                payload = self._build_unavailable_payload(
                    label,
                    "No nearby NWS observation station found.",
                )
            else:
                observation = await self._get_json(
                    f"/stations/{station_id}/observations/latest",
                    params={"require_qc": "true"},
                )
                payload = self._parse_current_weather(label, station_id, observation)
        except Exception as exc:
            logger.warning("Failed to fetch NWS weather for %s: %s", label, exc)
            payload = self._build_unavailable_payload(label, "Weather data is temporarily unavailable.")

        self._weather_cache[cache_key] = (now, payload)
        return payload
