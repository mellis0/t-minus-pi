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
    assert len(cfg.routes) == 1
    assert cfg.routes[0].id == "test_subway"
    assert cfg.routes[0].directions[0].headsign == "South"
