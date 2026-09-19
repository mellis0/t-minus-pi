from pathlib import Path
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
    env_file.write_text("MBTA_API_KEY=test_secret_key\nPORT=9999\n", encoding="utf-8")

    cfg = load_config(config_path=yaml_file, env_path=env_file)

    assert cfg.mbta_api_key == "test_secret_key"
    assert cfg.server.port == 9999  # env overrides yaml
    assert cfg.server.host == "127.0.0.1"
    assert cfg.server.refresh_seconds == 15
    assert cfg.display.title == "Test Transit"
    assert cfg.display.clock_format_24h is True
    assert cfg.display.show_alerts is False
    assert cfg.display.primary_text_scale == 1.25
    assert len(cfg.routes) == 2
    assert cfg.routes[0].id == "test_subway"
    assert cfg.routes[0].directions[0].headsign == "South"
    assert cfg.routes[1].bus_targets[0].route_id == "708"
    assert cfg.routes[1].bus_targets[0].route_name == "CT3"


def test_primary_text_scale_is_clamped(tmp_path: Path):
    yaml_file = tmp_path / "config.yaml"
    yaml_file.write_text(
        """
display:
  primary_text_scale: 4.0
""",
        encoding="utf-8",
    )

    cfg = load_config(config_path=yaml_file, env_path=tmp_path / ".env")

    assert cfg.display.primary_text_scale == 3.0


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
