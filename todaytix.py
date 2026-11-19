"""Stage shows in New York, Los Angeles, Chicago and Sydney from TodayTix (todaytix.com).

TodayTix city category pages carry the same schema.org TheaterEvent data as London
Theatre (same publisher), so the London parser is reused. robots.txt is respected.
"""
from __future__ import annotations

import datetime as dt

import londontheatre
import webfetch

BASE = "https://www.todaytix.com"
SOURCE = "TodayTix"
CITIES = {"us-new-york": "nyc", "us-los-angeles": "los-angeles", "us-chicago": "chicago", "au-sydney": "sydney"}
# Category page -> subcategory; specific pages first so their type wins.
CATEGORIES = [("dance", "BALLET"), ("musical-shows", "MUSICAL"), ("plays", "THEATRE"), ("broadway-shows", None), ("all-shows", None)]


def collect(cities: list[dict], city_dto, synced_at: str, today: dt.date | None = None, log=print) -> dict[str, list[dict]]:
    today = today or dt.date.today()
    by_id = {c["id"]: c for c in cities}
    result: dict[str, list[dict]] = {}
    for city_id, slug in CITIES.items():
        city = by_id.get(city_id)
        if city is None:
            continue
        events: dict[str, dict] = {}
        for category, sub in CATEGORIES:
            page = webfetch.get(f"{BASE}/{slug}/category/{category}")
            for event in londontheatre.shows(page, sub, city, city_dto, synced_at, today,
                                             base=BASE, source=SOURCE, prefix="tt") if page else []:
                events.setdefault(event["id"], event)
        log(f"  todaytix {city_id}: {len(events)} shows")
        if events:
            result[city_id] = list(events.values())
    return result
