import asyncio
import logging
from datetime import datetime, timedelta, timezone
from time import monotonic
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo
import httpx

from src.config import BusTargetConfig, DirectionConfig, RouteConfig

logger = logging.getLogger(__name__)

MBTA_API_BASE_URL = "https://api-v3.mbta.com"
ALERT_CACHE_TTL_SECONDS = 60.0
PREDICTION_FETCH_BUFFER = 1
BUS_TARGET_DISPLAY_LIMIT = 8
MBTA_SERVICE_TIMEZONE = ZoneInfo("America/New_York")
WORCESTER_OUTBOUND_STOP_ORDER = [
    ("NEC-2287", "South Station"),
    ("WML-0012-07", "Back Bay"),
    ("WML-0025-07", "Lansdowne"),
    ("WML-0035-01", "Boston Landing"),
    ("WML-0081-02", "Newtonville"),
    ("WML-0091-02", "West Newton"),
    ("WML-0102-02", "Auburndale"),
    ("WML-0125-02", "Wellesley Farms"),
    ("WML-0135-02", "Wellesley Hills"),
    ("WML-0147-02", "Wellesley Square"),
    ("WML-0177-02", "Natick Center"),
    ("WML-0199-02", "West Natick"),
    ("WML-0214-02", "Framingham"),
    ("WML-0252-01", "Ashland"),
    ("WML-0274-01", "Southborough"),
    ("WML-0340-01", "Westborough"),
    ("WML-0364-01", "Grafton"),
    ("WML-0442-CS", "Worcester"),
]


class MBTAClient:
    """Client for fetching and parsing real-time MBTA transit data."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = MBTA_API_BASE_URL,
        alert_cache_ttl_seconds: float = ALERT_CACHE_TTL_SECONDS,
        http_client: Optional[httpx.AsyncClient] = None,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.alert_cache_ttl_seconds = alert_cache_ttl_seconds
        self._alert_cache: Dict[tuple[Optional[str], Optional[str], tuple[str, ...]], tuple[float, Dict[str, Any]]] = {}
        self._trip_stops_cache: Dict[str, List[Dict[str, str]]] = {}
        self._http_client = http_client
        self._owns_http_client = http_client is None
        self.headers = {"Accept": "application/vnd.api+json"}
        if self.api_key:
            self.headers["x-api-key"] = self.api_key

    def _get_headers(self) -> Dict[str, str]:
        return self.headers

    def _get_http_client(self) -> httpx.AsyncClient:
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(timeout=10.0)
        return self._http_client

    async def aclose(self) -> None:
        if self._http_client is not None and self._owns_http_client:
            await self._http_client.aclose()
            self._http_client = None

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

    @staticmethod
    def _has_prediction_time(prediction: Dict[str, Any]) -> bool:
        attrs = prediction.get("attributes") or {}
        return bool(attrs.get("arrival_time") or attrs.get("departure_time"))

    @staticmethod
    def _is_unviable_prediction(prediction: Dict[str, Any]) -> bool:
        attrs = prediction.get("attributes") or {}
        return (
            attrs.get("schedule_relationship") == "CANCELLED"
            or not MBTAClient._has_prediction_time(prediction)
        )

    @staticmethod
    def _get_schedule_time_filters(now: Optional[datetime] = None) -> Dict[str, str]:
        """Build MBTA schedule filters for upcoming service-day times."""
        local_now = (now or datetime.now(timezone.utc)).astimezone(MBTA_SERVICE_TIMEZONE)
        service_date = local_now.date()
        service_hour = local_now.hour

        # MBTA treats trips between midnight and 3 AM as part of the previous
        # service day, represented with hours beyond 24:00.
        if service_hour < 3:
            service_date = service_date - timedelta(days=1)
            service_hour += 24

        return {
            "filter[date]": service_date.isoformat(),
            "filter[min_time]": f"{service_hour:02d}:{local_now.minute:02d}",
        }

    async def fetch_predictions_raw(
        self,
        stop_id: str,
        route_id: Optional[str] = None,
        route_filter: Optional[List[str]] = None,
        direction_id: Optional[int] = None,
        page_limit: Optional[int] = None,
        include_uncertainty: bool = False,
    ) -> Dict[str, Any]:
        """Query MBTA /predictions endpoint."""
        prediction_fields = [
            "arrival_time",
            "departure_time",
            "direction_id",
            "schedule_relationship",
            "status",
            "route",
            "stop",
            "trip",
            "vehicle",
        ]
        if include_uncertainty:
            prediction_fields.extend(["arrival_uncertainty", "departure_uncertainty"])

        params: Dict[str, Any] = {
            "filter[stop]": stop_id,
            "include": "trip,vehicle",
            "fields[prediction]": ",".join(prediction_fields),
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

        response = await self._get_http_client().get(
            f"{self.base_url}/predictions",
            params=params,
            headers=self._get_headers(),
        )
        response.raise_for_status()
        return response.json()

    async def fetch_schedules_raw(
        self,
        stop_id: str,
        route_id: Optional[str] = None,
        route_filter: Optional[List[str]] = None,
        direction_id: Optional[int] = None,
        page_limit: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Query MBTA /schedules endpoint."""
        params: Dict[str, Any] = {
            "filter[stop]": stop_id,
            "include": "trip,prediction",
            "fields[schedule]": ",".join([
                "arrival_time",
                "departure_time",
                "direction_id",
                "prediction",
                "trip",
            ]),
            "fields[prediction]": ",".join([
                "arrival_time",
                "departure_time",
                "direction_id",
                "schedule_relationship",
                "status",
            ]),
            "fields[trip]": "headsign,name",
            "sort": "time",
        }
        params.update(self._get_schedule_time_filters())

        if route_id:
            params["filter[route]"] = route_id
        elif route_filter and len(route_filter) > 0:
            params["filter[route]"] = ",".join(route_filter)

        if direction_id is not None:
            params["filter[direction_id]"] = direction_id

        if page_limit is not None:
            params["page[limit]"] = page_limit

        response = await self._get_http_client().get(
            f"{self.base_url}/schedules",
            params=params,
            headers=self._get_headers(),
        )
        response.raise_for_status()
        return response.json()

    async def fetch_trip_schedules_raw(self, trip_ids: List[str]) -> Dict[str, Any]:
        """Fetch scheduled stops for one or more trips."""
        params: Dict[str, Any] = {
            "filter[trip]": ",".join(trip_ids),
            "include": "stop",
            "fields[schedule]": "stop,trip",
            "fields[stop]": "name",
            "sort": "stop_sequence",
        }

        response = await self._get_http_client().get(
            f"{self.base_url}/schedules",
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
            "fields[alert]": "header,short_header",
        }
        if stop_id:
            params["filter[stop]"] = stop_id
        if route_id:
            params["filter[route]"] = route_id
        elif route_filter and len(route_filter) > 0:
            params["filter[route]"] = ",".join(route_filter)

        response = await self._get_http_client().get(
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

    @staticmethod
    def _alert_filter_kwargs_for_route(route_config: RouteConfig) -> Dict[str, Any]:
        """Prefer route-scoped alerts so station hubs do not leak unrelated line alerts."""
        if route_config.route_id:
            return {"route_id": route_config.route_id}
        if route_config.route_filter:
            return {"route_filter": route_config.route_filter}
        return {"stop_id": route_config.stop_id}

    def _parse_alerts(self, alert_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Extract alert fields used by the dashboard."""
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
        return parsed_alerts

    def _prediction_to_entry(
        self,
        prediction: Dict[str, Any],
        included: Dict[str, Dict[str, Any]],
        dir_overrides: Dict[int, str],
        scheduled_time_str: Optional[str] = None,
        bus_target: Optional[BusTargetConfig] = None,
    ) -> Optional[Dict[str, Any]]:
        attrs = prediction.get("attributes") or {}
        rel = prediction.get("relationships") or {}

        arrival_time_str = attrs.get("arrival_time")
        departure_time_str = attrs.get("departure_time")
        # Use arrival_time for ETA when available; departure_time remains useful
        # metadata for debugging and for predictions that omit an arrival.
        target_time_str = arrival_time_str or departure_time_str
        if not target_time_str:
            return None

        target_time = self._parse_time(target_time_str)
        if not target_time:
            return None

        # Skip past departures (more than 30 seconds ago)
        now = datetime.now(timezone.utc)
        if (target_time - now).total_seconds() < -30:
            return None

        direction_id = attrs.get("direction_id", 0)
        status = attrs.get("status")
        schedule_rel = attrs.get("schedule_relationship")

        trip_id = self._get_rel_id(rel, "trip")
        trip_item = included.get(f"trip:{trip_id}") if trip_id else None
        trip_attrs = (trip_item.get("attributes") or {}) if trip_item else {}
        headsign = trip_attrs.get("headsign")
        train_number = trip_attrs.get("name")

        r_id = self._get_rel_id(rel, "route")
        r_item = included.get(f"route:{r_id}") if r_id else None
        r_attrs = (r_item.get("attributes") or {}) if r_item else {}
        route_short_name = (
            r_attrs.get("short_name")
            or (bus_target.route_name if bus_target else None)
            or r_id
            or ""
        )
        route_color = r_attrs.get("color") or "DA291C"

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

        display_headsign = headsign or dir_overrides.get(direction_id, f"Direction {direction_id}")
        direction_label = bus_target.direction_name if bus_target else dir_overrides.get(direction_id)
        stop_name = bus_target.stop_name if bus_target else None

        return {
            "route_id": r_id,
            "route_name": route_short_name,
            "route_color": f"#{route_color}" if not route_color.startswith("#") else route_color,
            "headsign": display_headsign,
            "direction_label": direction_label,
            "stop_name": stop_name,
            "train_number": train_number or "",
            "minutes": minutes,
            "countdown": countdown_label,
            "time_iso": target_time_str,
            "arrival_time_iso": arrival_time_str,
            "departure_time_iso": departure_time_str,
            "scheduled_time_iso": scheduled_time_str,
            "predicted_time_iso": target_time_str,
            "time_formatted": target_time.strftime("%I:%M %p").lstrip("0"),
            "status": status,
            "schedule_relationship": schedule_rel,
            "vehicle_status": vehicle_status,
            "vehicle_stop_id": vehicle_stop_id,
            "is_predicted": True,
            "time_source": "prediction",
            "_trip_id": trip_id,
            "_stop_id": prediction_stop_id,
            "_sort_time": target_time,
        }

    @staticmethod
    def _has_live_bus_time(prediction: Dict[str, Any]) -> bool:
        attrs = prediction.get("attributes") or {}
        if attrs.get("arrival_time"):
            uncertainty = attrs.get("arrival_uncertainty")
        elif attrs.get("departure_time"):
            uncertainty = attrs.get("departure_uncertainty")
        else:
            return False

        if uncertainty is None:
            return True
        try:
            uncertainty_value = int(uncertainty)
        except (TypeError, ValueError):
            return False

        return uncertainty_value < 300 or uncertainty_value == 301

    def _schedule_to_entry(
        self,
        schedule: Dict[str, Any],
        included: Dict[str, Dict[str, Any]],
        dir_overrides: Dict[int, str],
        route_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        attrs = schedule.get("attributes") or {}
        rel = schedule.get("relationships") or {}

        arrival_time_str = attrs.get("arrival_time")
        departure_time_str = attrs.get("departure_time")
        target_time_str = arrival_time_str or departure_time_str
        if not target_time_str:
            return None

        target_time = self._parse_time(target_time_str)
        if not target_time:
            return None

        now = datetime.now(timezone.utc)
        if (target_time - now).total_seconds() < -30:
            return None

        direction_id = attrs.get("direction_id", 0)

        trip_id = self._get_rel_id(rel, "trip")
        trip_item = included.get(f"trip:{trip_id}") if trip_id else None
        trip_attrs = (trip_item.get("attributes") or {}) if trip_item else {}
        headsign = trip_attrs.get("headsign")
        train_number = trip_attrs.get("name")

        r_id = self._get_rel_id(rel, "route") or route_id
        display_headsign = headsign or dir_overrides.get(direction_id, f"Direction {direction_id}")
        minutes, countdown_label = self._format_countdown(target_time)

        return {
            "route_id": r_id,
            "route_name": r_id or "",
            "route_color": "#80276C",
            "headsign": display_headsign,
            "train_number": train_number or "",
            "minutes": minutes,
            "countdown": countdown_label,
            "time_iso": target_time_str,
            "arrival_time_iso": arrival_time_str,
            "departure_time_iso": departure_time_str,
            "scheduled_time_iso": target_time_str,
            "predicted_time_iso": None,
            "time_formatted": target_time.strftime("%I:%M %p").lstrip("0"),
            "status": None,
            "schedule_relationship": None,
            "vehicle_status": None,
            "vehicle_stop_id": None,
            "is_predicted": False,
            "time_source": "schedule",
            "_trip_id": trip_id,
            "_sort_time": target_time,
        }

    @staticmethod
    def _strip_private_entry_fields(entry: Dict[str, Any]) -> Dict[str, Any]:
        clean_entry = dict(entry)
        for key in list(clean_entry):
            if key.startswith("_"):
                clean_entry.pop(key, None)
        return clean_entry

    @staticmethod
    def _extract_trip_stops(schedule_data: Dict[str, Any]) -> List[Dict[str, str]]:
        included = MBTAClient._build_included_map(schedule_data.get("included", []) or [])
        stops = []
        for schedule in schedule_data.get("data", []) or []:
            rel = schedule.get("relationships") or {}
            stop_id = MBTAClient._get_rel_id(rel, "stop")
            stop_item = included.get(f"stop:{stop_id}") if stop_id else None
            stop_attrs = (stop_item.get("attributes") or {}) if stop_item else {}
            stop_name = stop_attrs.get("name")
            if stop_id and stop_name:
                stops.append({"id": stop_id, "name": stop_name})
        return stops

    @staticmethod
    def _extract_trip_stops_by_trip(schedule_data: Dict[str, Any]) -> Dict[str, List[Dict[str, str]]]:
        included = MBTAClient._build_included_map(schedule_data.get("included", []) or [])
        stops_by_trip: Dict[str, List[Dict[str, str]]] = {}
        for schedule in schedule_data.get("data", []) or []:
            rel = schedule.get("relationships") or {}
            trip_id = MBTAClient._get_rel_id(rel, "trip")
            stop_id = MBTAClient._get_rel_id(rel, "stop")
            stop_item = included.get(f"stop:{stop_id}") if stop_id else None
            stop_attrs = (stop_item.get("attributes") or {}) if stop_item else {}
            stop_name = stop_attrs.get("name")
            if trip_id and stop_id and stop_name:
                stops_by_trip.setdefault(trip_id, []).append({"id": stop_id, "name": stop_name})
        return stops_by_trip

    @staticmethod
    def _commuter_rail_service_detail(
        route_id: Optional[str],
        direction_id: int,
        trip_stops: List[Dict[str, str]],
    ) -> str:
        if route_id != "CR-Worcester" or direction_id != 0 or len(trip_stops) < 2:
            return ""

        canonical_names = [stop_name for _stop_id, stop_name in WORCESTER_OUTBOUND_STOP_ORDER]
        canonical_indices = {
            stop_name: index
            for index, stop_name in enumerate(canonical_names)
        }
        served_names = [stop["name"] for stop in trip_stops if stop["name"] in canonical_indices]
        if len(served_names) < 2:
            return ""

        try:
            start_index = canonical_indices[served_names[0]]
            end_index = canonical_indices[served_names[-1]]
        except KeyError:
            return ""

        expected_names = canonical_names[start_index:end_index + 1]
        missing_names = [stop_name for stop_name in expected_names if stop_name not in served_names]
        if not missing_names:
            return "Local"

        for previous_stop_name, next_stop_name in zip(served_names, served_names[1:]):
            previous_index = canonical_indices[previous_stop_name]
            next_index = canonical_indices[next_stop_name]
            if next_index - previous_index > 1:
                return (
                    f"Express to {next_stop_name} "
                    f"after {previous_stop_name}"
                )

        return "Express"

    async def _add_commuter_rail_service_details(
        self,
        entries: List[Dict[str, Any]],
        route_id: Optional[str],
        direction_id: int,
    ) -> None:
        if route_id != "CR-Worcester" or direction_id != 0:
            return

        entries_by_trip_id = {}
        for entry in entries:
            trip_id = entry.get("_trip_id")
            if trip_id:
                entries_by_trip_id[trip_id] = entry

        if not entries_by_trip_id:
            return

        missing_trip_ids = [
            trip_id
            for trip_id in entries_by_trip_id
            if trip_id not in self._trip_stops_cache
        ]
        if missing_trip_ids:
            trip_schedule_data = await self.fetch_trip_schedules_raw(missing_trip_ids)
            for trip_id, trip_stops in self._extract_trip_stops_by_trip(trip_schedule_data).items():
                self._trip_stops_cache[trip_id] = trip_stops

        for trip_id, entry in entries_by_trip_id.items():
            trip_stops = self._trip_stops_cache.get(trip_id, [])
            entry["service_detail"] = self._commuter_rail_service_detail(
                route_id,
                direction_id,
                trip_stops,
            )

    async def _get_commuter_rail_departures(
        self,
        route_config: RouteConfig,
        max_per_direction: int,
    ) -> Dict[str, Any]:
        """Fetch commuter rail departures from schedules, overlaid with predictions."""
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
            dir_overrides: Dict[int, str] = {}
            for d in route_config.directions:
                if d.headsign:
                    dir_overrides[d.direction_id] = d.headsign

            configured_dirs = [d.direction_id for d in route_config.directions] or [0, 1]
            page_limit = max_per_direction + PREDICTION_FETCH_BUFFER + 3

            schedule_responses, alert_data = await asyncio.gather(
                asyncio.gather(*[
                    self.fetch_schedules_raw(
                        stop_id=route_config.stop_id,
                        route_id=route_config.route_id,
                        route_filter=route_config.route_filter,
                        direction_id=d_id,
                        page_limit=page_limit,
                    )
                    for d_id in configured_dirs
                ]),
                self.fetch_alerts_cached(
                    **self._alert_filter_kwargs_for_route(route_config),
                ),
            )

            schedule_data = self._merge_prediction_data(schedule_responses)
            result["alerts"] = self._parse_alerts(alert_data)

            included_list = schedule_data.get("included", []) or []
            included = self._build_included_map(included_list)
            predictions = [
                included_item
                for included_item in included_list
                if included_item.get("type") == "prediction"
            ]

            predictions_by_id = {
                prediction.get("id"): prediction
                for prediction in predictions
                if prediction.get("id")
            }
            directions_map: Dict[int, List[Dict[str, Any]]] = {d_id: [] for d_id in configured_dirs}

            for schedule in schedule_data.get("data", []) or []:
                attrs = schedule.get("attributes") or {}
                rel = schedule.get("relationships") or {}
                direction_id = attrs.get("direction_id", 0)

                prediction_id = self._get_rel_id(rel, "prediction")
                matched_prediction = predictions_by_id.get(prediction_id) if prediction_id else None

                schedule_time_str = attrs.get("arrival_time") or attrs.get("departure_time")
                entry = self._schedule_to_entry(schedule, included, dir_overrides, route_config.route_id)
                if not entry:
                    continue

                if matched_prediction:
                    prediction_attrs = matched_prediction.get("attributes") or {}
                    prediction_time_str = (
                        prediction_attrs.get("arrival_time")
                        or prediction_attrs.get("departure_time")
                    )
                    prediction_time = self._parse_time(prediction_time_str)
                    if prediction_time and (prediction_time - datetime.now(timezone.utc)).total_seconds() >= -30:
                        minutes, countdown_label = self._format_countdown(
                            prediction_time,
                            prediction_attrs.get("status"),
                        )
                        entry.update({
                            "minutes": minutes,
                            "countdown": countdown_label,
                            "time_iso": prediction_time_str,
                            "arrival_time_iso": prediction_attrs.get("arrival_time"),
                            "departure_time_iso": prediction_attrs.get("departure_time"),
                            "predicted_time_iso": prediction_time_str,
                            "time_formatted": prediction_time.strftime("%I:%M %p").lstrip("0"),
                            "status": prediction_attrs.get("status"),
                            "schedule_relationship": prediction_attrs.get("schedule_relationship"),
                            "is_predicted": True,
                            "time_source": "prediction",
                            "_sort_time": prediction_time,
                        })

                if direction_id not in directions_map:
                    directions_map[direction_id] = []
                directions_map[direction_id].append(entry)

            for d_id in configured_dirs:
                entries = directions_map.get(d_id, [])
                entries.sort(key=lambda x: x["_sort_time"])
                limited_entries_with_private_fields = entries[:max_per_direction]
                await self._add_commuter_rail_service_details(
                    limited_entries_with_private_fields,
                    route_config.route_id,
                    d_id,
                )
                limited_entries = [
                    self._strip_private_entry_fields(entry)
                    for entry in limited_entries_with_private_fields
                ]

                dir_name = dir_overrides.get(d_id, f"Direction {d_id}")
                result["directions"].append({
                    "direction_id": d_id,
                    "name": dir_name,
                    "departures": limited_entries,
                })

        except Exception as e:
            logger.error(f"Error fetching commuter rail departures for {route_config.id}: {e}", exc_info=True)
            result["error"] = str(e)

        return result

    async def _get_bus_target_departures(
        self,
        route_config: RouteConfig,
        max_per_direction: int,
    ) -> Dict[str, Any]:
        """Fetch live bus ETAs for configured route/stop/direction targets."""
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
            targets = route_config.bus_targets
            fetch_limit = BUS_TARGET_DISPLAY_LIMIT + PREDICTION_FETCH_BUFFER

            prediction_responses = await asyncio.gather(*[
                self.fetch_predictions_raw(
                    stop_id=target.stop_id,
                    route_id=target.route_id,
                    direction_id=target.direction_id,
                    page_limit=fetch_limit,
                    include_uncertainty=True,
                )
                for target in targets
            ])

            live_entries: List[Dict[str, Any]] = []
            for target, pred_data in zip(targets, prediction_responses):
                included = self._build_included_map(pred_data.get("included", []))

                for prediction in pred_data.get("data", []) or []:
                    attrs = prediction.get("attributes") or {}
                    rel = prediction.get("relationships") or {}
                    if attrs.get("direction_id") != target.direction_id:
                        continue
                    if self._get_rel_id(rel, "route") != target.route_id:
                        continue
                    if not self._has_live_bus_time(prediction):
                        continue

                    entry = self._prediction_to_entry(
                        prediction,
                        included,
                        {},
                        bus_target=target,
                    )
                    if entry:
                        live_entries.append(entry)

            live_entries.sort(key=lambda x: x["_sort_time"])
            result["directions"].append({
                "direction_id": 0,
                "name": "Live ETA",
                "departures": [
                    self._strip_private_entry_fields(entry)
                    for entry in live_entries[:BUS_TARGET_DISPLAY_LIMIT]
                ],
            })

            target_route_ids = list(dict.fromkeys(target.route_id for target in targets))
            alert_responses = [
                await self.fetch_alerts_cached(route_filter=target_route_ids)
            ]
            seen_alerts = set()
            parsed_alerts: List[Dict[str, Any]] = []
            for alert_data in alert_responses:
                for alert in self._parse_alerts(alert_data):
                    key = alert.get("id") or alert.get("header")
                    if key in seen_alerts:
                        continue
                    seen_alerts.add(key)
                    parsed_alerts.append(alert)
            result["alerts"] = parsed_alerts

        except Exception as e:
            logger.error(f"Error fetching bus target departures for {route_config.id}: {e}", exc_info=True)
            result["error"] = str(e)

        return result

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
            if route_config.type == "commuter_rail":
                return await self._get_commuter_rail_departures(
                    route_config,
                    max_per_direction=max_per_direction,
                )
            if route_config.type == "bus" and route_config.bus_targets:
                return await self._get_bus_target_departures(
                    route_config,
                    max_per_direction=max_per_direction,
                )

            # Group predictions by direction or route
            dir_overrides: Dict[int, str] = {}
            for d in route_config.directions:
                if d.headsign:
                    dir_overrides[d.direction_id] = d.headsign

            configured_dirs = [d.direction_id for d in route_config.directions] or [0, 1]
            initial_page_limit = max_per_direction + PREDICTION_FETCH_BUFFER

            # 1. Fetch Predictions
            prediction_responses = await asyncio.gather(*[
                self.fetch_predictions_raw(
                    stop_id=route_config.stop_id,
                    route_id=route_config.route_id,
                    route_filter=route_config.route_filter,
                    direction_id=d_id,
                    page_limit=initial_page_limit,
                )
                for d_id in configured_dirs
            ])

            prediction_data_by_direction = {
                d_id: response
                for d_id, response in zip(configured_dirs, prediction_responses)
            }

            # 2. Fetch Alerts
            alert_data = await self.fetch_alerts_cached(
                **self._alert_filter_kwargs_for_route(route_config),
            )

            pred_data = self._merge_prediction_data(list(prediction_data_by_direction.values()))
            included = self._build_included_map(pred_data.get("included", []))
            predictions = pred_data.get("data", []) or []

            result["alerts"] = self._parse_alerts(alert_data)

            directions_map: Dict[int, List[Dict[str, Any]]] = {d_id: [] for d_id in configured_dirs}
            for p in predictions:
                entry = self._prediction_to_entry(p, included, dir_overrides)
                if not entry:
                    continue

                direction_id = (p.get("attributes") or {}).get("direction_id", 0)

                if direction_id not in directions_map:
                    directions_map[direction_id] = []
                directions_map[direction_id].append(entry)

            retry_dirs = []
            for d_id in configured_dirs:
                raw_predictions = prediction_data_by_direction[d_id].get("data", []) or []
                unviable_count = sum(
                    1
                    for prediction in raw_predictions
                    if self._is_unviable_prediction(prediction)
                )
                if (
                    len(directions_map.get(d_id, [])) < max_per_direction
                    and len(raw_predictions) == initial_page_limit
                    and unviable_count > 0
                ):
                    retry_dirs.append(d_id)

            if retry_dirs:
                retry_responses = await asyncio.gather(*[
                    self.fetch_predictions_raw(
                        stop_id=route_config.stop_id,
                        route_id=route_config.route_id,
                        route_filter=route_config.route_filter,
                        direction_id=d_id,
                        page_limit=None,
                    )
                    for d_id in retry_dirs
                ])
                for d_id, response in zip(retry_dirs, retry_responses):
                    prediction_data_by_direction[d_id] = response

                pred_data = self._merge_prediction_data(list(prediction_data_by_direction.values()))
                included = self._build_included_map(pred_data.get("included", []))
                directions_map = {d_id: [] for d_id in configured_dirs}

                for p in pred_data.get("data", []) or []:
                    entry = self._prediction_to_entry(p, included, dir_overrides)
                    if not entry:
                        continue

                    direction_id = (p.get("attributes") or {}).get("direction_id", 0)

                    if direction_id not in directions_map:
                        directions_map[direction_id] = []
                    directions_map[direction_id].append(entry)

            for d_id in configured_dirs:
                entries = directions_map.get(d_id, [])
                entries.sort(key=lambda x: x["_sort_time"])
                limited_entries = [
                    self._strip_private_entry_fields(entry)
                    for entry in entries[:max_per_direction]
                ]

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
