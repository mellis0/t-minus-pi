import asyncio
import logging
from datetime import datetime, timedelta, timezone
from time import monotonic
from typing import Any, Dict, List, Optional
import httpx

from src.config import DirectionConfig, RouteConfig

logger = logging.getLogger(__name__)

MBTA_API_BASE_URL = "https://api-v3.mbta.com"
ALERT_CACHE_TTL_SECONDS = 60.0
PREDICTION_FETCH_BUFFER = 1


class MBTAClient:
    """Client for fetching and parsing real-time MBTA transit data."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = MBTA_API_BASE_URL,
        alert_cache_ttl_seconds: float = ALERT_CACHE_TTL_SECONDS,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.alert_cache_ttl_seconds = alert_cache_ttl_seconds
        self._alert_cache: Dict[tuple[Optional[str], Optional[str], tuple[str, ...]], tuple[float, Dict[str, Any]]] = {}
        self.headers = {"Accept": "application/vnd.api+json"}
        if self.api_key:
            self.headers["x-api-key"] = self.api_key

    def _get_headers(self) -> Dict[str, str]:
        return self.headers

    @staticmethod
    def _parse_time(time_str: Optional[str]) -> Optional[datetime]:
        """Parse ISO 8601 timestamp to timezone-aware datetime."""
        if not time_str:
            return None
        try:
            return datetime.fromisoformat(time_str)
        except Exception:
            return None

    @staticmethod
    def _format_countdown(
        target_time: datetime,
        status_text: Optional[str] = None,
        vehicle_status: Optional[str] = None,
        vehicle_stop_id: Optional[str] = None,
        prediction_stop_id: Optional[str] = None,
    ) -> tuple[int, str]:
        """Calculate minutes until the displayed prediction time."""
        now = datetime.now(timezone.utc)
        diff_seconds = (target_time - now).total_seconds()

        if status_text:
            normalized_status = status_text.lower()
            if "board" in normalized_status:
                return 0, "BRD"
            if "arriv" in normalized_status:
                return 0, "ARR"

        if (
            vehicle_status == "STOPPED_AT"
            and vehicle_stop_id
            and prediction_stop_id
            and vehicle_stop_id == prediction_stop_id
            and now >= target_time - timedelta(seconds=15)
        ):
            return 0, "BRD"

        # Conservative thresholds matching MBTA platform signs:
        if diff_seconds <= 30:
            return 0, "ARR"
        elif diff_seconds < 120:
            return 1, "1 min"
        else:
            minutes = int(diff_seconds // 60)
            return minutes, f"{minutes} min"

    @staticmethod
    def _build_included_map(included_list: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        """Index included resources by (type, id) for quick O(1) lookup."""
        lookup = {}
        for item in included_list or []:
            key = f"{item.get('type')}:{item.get('id')}"
            lookup[key] = item
        return lookup

    @staticmethod
    def _get_rel_id(rel: Dict[str, Any], key: str) -> Optional[str]:
        """Safely extract relationship resource ID from JSON:API structure."""
        if not isinstance(rel, dict):
            return None
        item = rel.get(key)
        if isinstance(item, dict):
            data = item.get("data")
            if isinstance(data, dict):
                return data.get("id")
        return None

    async def fetch_predictions_raw(
        self,
        stop_id: str,
        route_id: Optional[str] = None,
        route_filter: Optional[List[str]] = None,
        direction_id: Optional[int] = None,
        page_limit: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Query MBTA /predictions endpoint."""
        params: Dict[str, Any] = {
            "filter[stop]": stop_id,
            "include": "trip,vehicle",
            "fields[prediction]": ",".join([
                "arrival_time",
                "departure_time",
                "direction_id",
                "schedule_relationship",
                "status",
                "route",
                "stop",
                "trip",
                "vehicle",
            ]),
            "fields[trip]": "headsign,name",
            "fields[vehicle]": "current_status,stop",
            "sort": "time",
        }

        if route_id:
            params["filter[route]"] = route_id
        elif route_filter and len(route_filter) > 0:
            params["filter[route]"] = ",".join(route_filter)

        if direction_id is not None:
            params["filter[direction_id]"] = direction_id

        if page_limit is not None:
            params["page[limit]"] = page_limit

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{self.base_url}/predictions",
                params=params,
                headers=self._get_headers(),
            )
            response.raise_for_status()
            return response.json()

    @staticmethod
    def _merge_prediction_data(responses: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Merge per-direction prediction responses into one JSON:API-style payload."""
        merged: Dict[str, Any] = {"data": [], "included": []}
        included_seen = set()

        for response in responses:
            merged["data"].extend(response.get("data", []) or [])
            for included_item in response.get("included", []) or []:
                key = (included_item.get("type"), included_item.get("id"))
                if key not in included_seen:
                    merged["included"].append(included_item)
                    included_seen.add(key)

        return merged

    async def fetch_alerts_raw(
        self,
        stop_id: Optional[str] = None,
        route_id: Optional[str] = None,
        route_filter: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Query MBTA /alerts endpoint for active disruptions."""
        params: Dict[str, Any] = {
            "filter[datetime]": "NOW",
            "fields[alert]": "header,short_header,effect,severity",
        }
        if stop_id:
            params["filter[stop]"] = stop_id
        if route_id:
            params["filter[route]"] = route_id
        elif route_filter and len(route_filter) > 0:
            params["filter[route]"] = ",".join(route_filter)

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{self.base_url}/alerts",
                params=params,
                headers=self._get_headers(),
            )
            response.raise_for_status()
            return response.json()

    async def fetch_alerts_cached(
        self,
        stop_id: Optional[str] = None,
        route_id: Optional[str] = None,
        route_filter: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Fetch alerts with a short cache so frequent prediction refreshes do not spam alerts."""
        cache_key = (stop_id, route_id, tuple(route_filter or []))
        now = monotonic()
        cached = self._alert_cache.get(cache_key)
        if cached:
            fetched_at, data = cached
            if now - fetched_at < self.alert_cache_ttl_seconds:
                return data

        data = await self.fetch_alerts_raw(
            stop_id=stop_id,
            route_id=route_id,
            route_filter=route_filter,
        )
        self._alert_cache[cache_key] = (now, data)
        return data

    async def get_route_departures(
        self,
        route_config: RouteConfig,
        max_per_direction: int = 3,
    ) -> Dict[str, Any]:
        """Fetch and process structured departure cards and alerts for a single route config."""
        result: Dict[str, Any] = {
            "id": route_config.id,
            "name": route_config.name,
            "type": route_config.type,
            "stop_id": route_config.stop_id,
            "route_id": route_config.route_id,
            "directions": [],
            "alerts": [],
            "error": None,
        }

        try:
            # Group predictions by direction or route
            dir_overrides: Dict[int, str] = {}
            for d in route_config.directions:
                if d.headsign:
                    dir_overrides[d.direction_id] = d.headsign

            configured_dirs = [d.direction_id for d in route_config.directions] or [0, 1]

            # 1. Fetch Predictions
            prediction_responses = await asyncio.gather(*[
                self.fetch_predictions_raw(
                    stop_id=route_config.stop_id,
                    route_id=route_config.route_id,
                    route_filter=route_config.route_filter,
                    direction_id=d_id,
                    page_limit=max_per_direction + PREDICTION_FETCH_BUFFER,
                )
                for d_id in configured_dirs
            ])
            pred_data = self._merge_prediction_data(prediction_responses)

            # 2. Fetch Alerts
            alert_data = await self.fetch_alerts_cached(
                stop_id=route_config.stop_id,
                route_id=route_config.route_id,
                route_filter=route_config.route_filter,
            )

            included = self._build_included_map(pred_data.get("included", []))
            predictions = pred_data.get("data", []) or []

            # Parse alerts
            parsed_alerts = []
            for alert_item in alert_data.get("data", []) or []:
                attrs = alert_item.get("attributes") or {}
                header = attrs.get("header") or attrs.get("short_header")
                effect = attrs.get("effect", "UNKNOWN")
                severity = attrs.get("severity", 0)
                if header:
                    parsed_alerts.append({
                        "id": alert_item.get("id"),
                        "header": header,
                        "effect": effect,
                        "severity": severity,
                    })
            result["alerts"] = parsed_alerts

            directions_map: Dict[int, List[Dict[str, Any]]] = {0: [], 1: []}

            for p in predictions:
                attrs = p.get("attributes") or {}
                rel = p.get("relationships") or {}

                arrival_time_str = attrs.get("arrival_time")
                departure_time_str = attrs.get("departure_time")
                # Use arrival_time for ETA when available; departure_time remains useful
                # metadata for debugging and for predictions that omit an arrival.
                target_time_str = arrival_time_str or departure_time_str
                if not target_time_str:
                    continue

                target_time = self._parse_time(target_time_str)
                if not target_time:
                    continue

                # Skip past departures (more than 30 seconds ago)
                now = datetime.now(timezone.utc)
                if (target_time - now).total_seconds() < -30:
                    continue

                direction_id = attrs.get("direction_id", 0)
                status = attrs.get("status")
                schedule_rel = attrs.get("schedule_relationship")

                # Safely extract trip details
                trip_id = self._get_rel_id(rel, "trip")
                trip_item = included.get(f"trip:{trip_id}") if trip_id else None
                trip_attrs = (trip_item.get("attributes") or {}) if trip_item else {}
                headsign = trip_attrs.get("headsign")
                train_number = trip_attrs.get("name")

                # Safely extract route details
                r_id = self._get_rel_id(rel, "route")
                r_item = included.get(f"route:{r_id}") if r_id else None
                r_attrs = (r_item.get("attributes") or {}) if r_item else {}
                route_short_name = r_attrs.get("short_name") or r_id or ""
                route_color = r_attrs.get("color") or "DA291C"

                # Safely extract vehicle details
                vehicle_id = self._get_rel_id(rel, "vehicle")
                vehicle_item = included.get(f"vehicle:{vehicle_id}") if vehicle_id else None
                v_attrs = (vehicle_item.get("attributes") or {}) if vehicle_item else {}
                v_rel = (vehicle_item.get("relationships") or {}) if vehicle_item else {}
                vehicle_status = v_attrs.get("current_status")
                vehicle_stop_id = self._get_rel_id(v_rel, "stop")
                prediction_stop_id = self._get_rel_id(rel, "stop")

                minutes, countdown_label = self._format_countdown(
                    target_time,
                    status,
                    vehicle_status=vehicle_status,
                    vehicle_stop_id=vehicle_stop_id,
                    prediction_stop_id=prediction_stop_id,
                )

                # Determine effective headsign
                display_headsign = headsign or dir_overrides.get(direction_id, f"Direction {direction_id}")

                entry = {
                    "route_id": r_id,
                    "route_name": route_short_name,
                    "route_color": f"#{route_color}" if not route_color.startswith("#") else route_color,
                    "headsign": display_headsign,
                    "train_number": train_number or "",
                    "minutes": minutes,
                    "countdown": countdown_label,
                    "time_iso": target_time_str,
                    "arrival_time_iso": arrival_time_str,
                    "departure_time_iso": departure_time_str,
                    "time_formatted": target_time.strftime("%I:%M %p").lstrip("0"),
                    "status": status,
                    "schedule_relationship": schedule_rel,
                    "vehicle_status": vehicle_status,
                    "vehicle_stop_id": vehicle_stop_id,
                }

                if direction_id not in directions_map:
                    directions_map[direction_id] = []
                directions_map[direction_id].append(entry)

            for d_id in configured_dirs:
                entries = directions_map.get(d_id, [])
                entries.sort(key=lambda x: x["minutes"])
                limited_entries = entries[:max_per_direction]

                dir_name = dir_overrides.get(d_id, f"Direction {d_id}")
                result["directions"].append({
                    "direction_id": d_id,
                    "name": dir_name,
                    "departures": limited_entries,
                })

        except Exception as e:
            logger.error(f"Error fetching departures for {route_config.id}: {e}", exc_info=True)
            result["error"] = str(e)

        return result

    async def get_all_departures(
        self,
        routes: List[RouteConfig],
        max_per_direction: int = 3,
    ) -> List[Dict[str, Any]]:
        """Fetch departures for all configured routes concurrently."""
        results = []
        for route in routes:
            card = await self.get_route_departures(route, max_per_direction=max_per_direction)
            results.append(card)
        return results
