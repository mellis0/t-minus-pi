# t-minus-pi

A lightweight Python application that fetches real-time MBTA transit data and renders a clean, glanceable web dashboard optimized for a Raspberry Pi display.

## Overview

t-minus-pi connects to the MBTA V3 API to retrieve real-time departure predictions, vehicle positions, and service alerts across subway, commuter rail, and bus routes. The dashboard is designed to run in fullscreen kiosk mode on a Raspberry Pi connected to an HDMI display, providing an at-a-glance departure board for daily transit tracking.

Tracked stops, routes, directions, and display names are fully customizable through a simple configuration file.

## Target Hardware and Environment

- Device: Raspberry Pi 4 (4GB RAM) running Raspberry Pi OS.
- Display: 1080p (1920x1080) HDMI portable monitor.
- Display Mode: Fullscreen Chromium kiosk mode with screen blanking disabled.

## Getting Started

### Prerequisites

- Python 3.10 or higher
- MBTA API key (obtainable at https://api-v3.mbta.com/register)

### Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/mellis0/t-minus-pi.git
   cd t-minus-pi
   ```

2. Create and activate a virtual environment:
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   ```

3. Install project dependencies:
   ```bash
   pip install -r requirements.txt
   ```

### Environment Configuration

Store secrets such as your MBTA API key in a `.env` file in the root directory. Copy the sample file:

```bash
cp .env.example .env
```

Set your API key in `.env`:

```env
MBTA_API_KEY=your_api_key_here
PORT=8000
HOST=0.0.0.0
```

### Route Configuration

Define the routes and stops you want to display in `config.yaml`. Copy the sample configuration:

```bash
cp config.example.yaml config.yaml
```

Example configuration structure:

```yaml
server:
  host: "0.0.0.0"
  port: 8000
  refresh_seconds: 20

display:
  title: "MBTA Transit Departures"
  clock_format_24h: false

routes:
  # Subway Example
  - id: "subway_line"
    type: "subway"
    route_id: "Red"
    stop_id: "place-dwnrn"
    name: "Downtown Crossing - Red Line"
    directions:
      - direction_id: 0
        headsign_override: "Southbound"
      - direction_id: 1
        headsign_override: "Northbound"

  # Commuter Rail Example
  - id: "commuter_rail_line"
    type: "commuter_rail"
    route_id: "CR-Fitchburg"
    stop_id: "place-north"
    name: "North Station - Fitchburg Line"
    directions:
      - direction_id: 0
        headsign_override: "Outbound"
      - direction_id: 1
        headsign_override: "Inbound"

  # Bus Example
  - id: "local_bus_routes"
    type: "bus"
    stop_id: "place-harsq"
    name: "Harvard Square - Local Buses"
    route_filter: ["1", "66", "77"]
```

### Running the Application

Start the local web server:

```bash
python -m uvicorn src.app:app --host 0.0.0.0 --port 8000 --reload
```

The dashboard will be accessible at `http://localhost:8000`.

## Planned Features (TODO)

- MBTA API client: Async Python client using httpx to query predictions, schedules, and alerts from the MBTA V3 API.
- Configuration loader: Validation and parsing for config.yaml and .env variables.
- Kiosk frontend: High-contrast, glanceable 1080p user interface.
- Live clock: Digital clock with date and second-by-second updates.
- Departure cards: Color-coded cards for subway, commuter rail, and bus routes displaying countdowns in minutes.
- Service alerts: Status banners for delays, disruptions, and elevator outages impacting tracked routes.
- Raspberry Pi deployment: Autostart kiosk configuration script and systemd service file.
- Automated testing: Unit tests and mock API tests using pytest.

## Future Roadmap (Optional Features)

- Weather widget: Local temperature and weather conditions display.
- Walking time calculation: Adjust departure countdowns based on walking distance to stations, including leave-now indicators.
- Boston sports schedule: Event and game day schedule integration for local teams (Bruins, Celtics, Red Sox, Patriots) with crowd and delay warnings.
- Configuration GUI: Web-based settings interface to search MBTA stops and manage configuration without modifying YAML files directly.
- Network resilience: Offline status indicators and automatic reconnection handling.

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.
