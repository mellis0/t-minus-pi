import asyncio
from src.config import load_config
from src.mbta_client import MBTAClient


async def main():
    cfg = load_config()
    print("API Key configured:", bool(cfg.mbta_api_key))
    client = MBTAClient(api_key=cfg.mbta_api_key)
    cards = await client.get_all_departures(cfg.routes)
    print(f"Total cards returned: {len(cards)}")
    for card in cards:
        print(f"\n--- {card['name']} ({card['type']}) ---")
        if card["error"]:
            print(f"  Error: {card['error']}")
        for d in card["directions"]:
            print(f"  Direction: {d['name']}")
            for dep in d["departures"]:
                print(f"    - {dep['route_name']} {dep['headsign']} | {dep['time_formatted']} ({dep['countdown']})")
        if card["alerts"]:
            print(f"  Alerts: {len(card['alerts'])}")
            for a in card["alerts"]:
                print(f"    ! {a['header']}")


if __name__ == "__main__":
    asyncio.run(main())
