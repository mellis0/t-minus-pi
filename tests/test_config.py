from pathlib import Path
import os
from src.config import load_config


def test_load_config_defaults(tmp_path: Path):
    yaml_file = tmp_path / "config.yaml"
    yaml_file.write_text(
        """
server:
  host: "127.0.0.1"
  port: 9000
  refresh_seconds: 15

display:
  title: "Test Transit"
  clock_format_24h: true
  show_alerts: false
  primary_text_scale: 1.25

weather:
  enabled: true
  label: "Cambridge, MA"
  latitude: 42.3736
  longitude: -71.1097
  refresh_seconds: 600
  text_scale: 1.4

routes:
  - id: "test_subway"
    type: "subway"
    route_id: "Red"
    stop_id: "place-test"
    name: "Test Station"
    directions:
      - direction_id: 0
        headsign: "South"
      - direction_id: 1
        headsign: "North"
  - id: "test_buses"
    type: "bus"
    stop_id: "place-andrw"
    name: "Test Buses"
    bus_targets:
      - route_id: "708"
        route_name: "CT3"
        stop_id: "place-andrw"
        stop_name: "Andrew"
        direction_id: 0
        direction_name: "Outbound"
""",
        encoding="utf-8",
    )

    env_file = tmp_path / ".env"
    env_file.write_text(
        "MBTA_API_KEY=test_secret_key\nPORT=9999\nNWS_USER_AGENT=t-minus-pi-test (test@example.com)\n",
        encoding="utf-8",
    )

    cfg = load_config(config_path=yaml_file, env_path=env_file)

    assert cfg.mbta_api_key == "test_secret_key"
    assert cfg.nws_user_agent == "t-minus-pi-test (test@example.com)"
    assert cfg.server.port == 9999  # env overrides yaml
    assert cfg.server.host == "127.0.0.1"
    assert cfg.server.refresh_seconds == 15
    assert cfg.display.title == "Test Transit"
    assert cfg.display.clock_format_24h is True
    assert cfg.display.show_alerts is False
    assert cfg.display.primary_text_scale == 1.25
    assert cfg.weather.enabled is True
    assert cfg.weather.label == "Cambridge, MA"
    assert cfg.weather.latitude == 42.3736
    assert cfg.weather.longitude == -71.1097
    assert cfg.weather.refresh_seconds == 600
    assert cfg.weather.text_scale == 1.4
    assert len(cfg.routes) == 2
    assert cfg.routes[0].id == "test_subway"
    assert cfg.routes[0].directions[0].headsign == "South"
    assert cfg.routes[1].bus_targets[0].route_id == "708"
    assert cfg.routes[1].bus_targets[0].route_name == "CT3"


def test_primary_text_scale_has_no_upper_limit(tmp_path: Path):
    yaml_file = tmp_path / "config.yaml"
    yaml_file.write_text(
        """
display:
  primary_text_scale: 4.0
""",
        encoding="utf-8",
    )

    cfg = load_config(config_path=yaml_file, env_path=tmp_path / ".env")

    assert cfg.display.primary_text_scale == 4.0


def test_primary_text_scale_has_minimum(tmp_path: Path):
    yaml_file = tmp_path / "config.yaml"
    yaml_file.write_text(
        """
display:
  primary_text_scale: 0.2
""",
        encoding="utf-8",
    )

    cfg = load_config(config_path=yaml_file, env_path=tmp_path / ".env")

    assert cfg.display.primary_text_scale == 0.8


def test_invalid_primary_text_scale_uses_default(tmp_path: Path):
    yaml_file = tmp_path / "config.yaml"
    yaml_file.write_text(
        """
display:
  primary_text_scale: huge
""",
        encoding="utf-8",
    )

    cfg = load_config(config_path=yaml_file, env_path=tmp_path / ".env")

    assert cfg.display.primary_text_scale == 1.0


def test_non_finite_primary_text_scale_uses_default(tmp_path: Path):
    yaml_file = tmp_path / "config.yaml"
    yaml_file.write_text(
        """
display:
  primary_text_scale: .inf
""",
        encoding="utf-8",
    )

    cfg = load_config(config_path=yaml_file, env_path=tmp_path / ".env")

    assert cfg.display.primary_text_scale == 1.0


def test_weather_defaults_when_not_configured(tmp_path: Path):
    yaml_file = tmp_path / "config.yaml"
    env_file = tmp_path / ".env"
    yaml_file.write_text("routes: []\n", encoding="utf-8")
    env_file.write_text("", encoding="utf-8")

    old_nws_user_agent = os.environ.pop("NWS_USER_AGENT", None)
    try:
        cfg = load_config(config_path=yaml_file, env_path=env_file)
    finally:
        if old_nws_user_agent is not None:
            os.environ["NWS_USER_AGENT"] = old_nws_user_agent

    assert cfg.nws_user_agent is None
    assert cfg.weather.enabled is False
    assert cfg.weather.label is None
    assert cfg.weather.latitude is None
    assert cfg.weather.longitude is None
    assert cfg.weather.refresh_seconds == 600
    assert cfg.weather.text_scale == 1.0


def test_weather_text_scale_has_minimum(tmp_path: Path):
    yaml_file = tmp_path / "config.yaml"
    yaml_file.write_text(
        """
weather:
  text_scale: 0.2
""",
        encoding="utf-8",
    )

    cfg = load_config(config_path=yaml_file, env_path=tmp_path / ".env")

    assert cfg.weather.text_scale == 0.8


def test_invalid_weather_text_scale_uses_default(tmp_path: Path):
    yaml_file = tmp_path / "config.yaml"
    yaml_file.write_text(
        """
weather:
  text_scale: giant
""",
        encoding="utf-8",
    )

    cfg = load_config(config_path=yaml_file, env_path=tmp_path / ".env")

    assert cfg.weather.text_scale == 1.0
