"""Concerts in every non-Turkish city from Songkick metro-area pages (songkick.com).

Each metro page lists upcoming concerts and festivals as schema.org MusicEvent data
(JSON-LD): name, artist image, date (and often time), venue with address and the
Songkick event page, which links on to the ticket sellers. robots.txt is respected.
"""
from __future__ import annotations

import datetime as dt
import re

import webfetch
from biletinial import clean, _https

BASE = "https://www.songkick.com"
SOURCE = "Songkick"
PAGES = 4            # 50 concerts per page
HORIZON_DAYS = 90
METROS = {
    "gb-london": "24426-uk-london", "gb-manchester": "24475-uk-manchester", "ie-dublin": "29314-ireland-dublin",
    "de-berlin": "28443-germany-berlin", "de-hamburg": "28498-germany-hamburg", "de-munich": "28549-germany-munich",
    "nl-amsterdam": "31366-netherlands-amsterdam", "be-brussels": "182976-belgium-brussels",
    "at-vienna": "26771-austria-vienna", "ch-zurich": "104761-switzerland-zurich", "es-madrid": "28755-spain-madrid",
    "es-barcelona": "28714-spain-barcelona", "fr-paris": "28909-france-paris", "it-milan": "30338-italy-milan",
    "se-stockholm": "32252-sweden-stockholm", "dk-copenhagen": "28617-denmark-copenhagen",
    "us-new-york": "7644-us-new-york-nyc", "us-los-angeles": "17835-us-los-angeles-la", "us-chicago": "9426-us-chicago",
    "ca-toronto": "27396-canada-toronto", "au-sydney": "26794-australia-sydney", "it-rome": "30366-italy-rome",
    "cz-prague": "28425-czech-republic-prague", "hu-budapest": "29047-hungary-budapest", "gr-athens": "28976-greece-athens",
    "pt-lisbon": "31802-portugal-lisbon", "ae-dubai": "26664-united-arab-emirates-dubai",
}
GENRES = [("CLASSICAL", ("classical", "opera", "orchestra", "baroque", "choral")),
          ("JAZZ", ("jazz", "blues", "soul", "funk")),
          ("ELECTRONIC", ("electronic", "dance", "house", "techno", "trance", "drum_and_bass", "edm", "dubstep")),
          ("ROCK", ("rock", "metal", "punk", "indie", "alternative", "grunge", "emo", "hardcore")),
          ("POP", ("pop", "r_and_b", "hip_hop", "rap", "latin", "k_pop", "singer_songwriter", "country"))]


def genre(item: dict, url: str) -> str | None:
    if "/festivals/" in url:
        return "FESTIVAL"
    performers = item.get("performer") or []
    tags = " ".join(str(g).lower() for p in (performers if isinstance(performers, list) else [performers])
                    if isinstance(p, dict) for g in (p.get("genre") or []))
    for name, words in GENRES:
        if any(w in tags for w in words):
            return name
    return None


def _utc(local: dt.datetime, timezone: str) -> str:
    try:
        from zoneinfo import ZoneInfo
        aware = local.replace(tzinfo=ZoneInfo(timezone))
    except Exception:  # no time zone database: treat as UTC (only the stored instant shifts a little)
        aware = local.replace(tzinfo=dt.timezone.utc)
    return aware.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def concerts(page: str, city: dict, city_dto, synced_at: str, today: dt.date) -> list[dict]:
    out = []
    horizon = today + dt.timedelta(days=HORIZON_DAYS)
    for item in webfetch.json_ld(page):
        if item.get("@type") != "MusicEvent":
            continue
        url = _https(str(item.get("url") or "").split("?")[0])
        title = clean(item.get("name"))
        start = str(item.get("startDate") or "")
        if not url or not url.startswith(BASE + "/") or not title or not start:
            continue
        if "Cancelled" in str(item.get("eventStatus")) or "Postponed" in str(item.get("eventStatus")):
            continue
        try:
            when = dt.datetime.fromisoformat(start[:19]) if "T" in start else dt.datetime.fromisoformat(start[:10] + "T20:00:00")
            end = dt.date.fromisoformat(str(item.get("endDate") or start)[:10])
        except ValueError:
            continue
        day = when.date()
        if end < today or day > horizon:
            continue
        first = max(day, today)
        location = item.get("location") or {}
        venue_name = clean(location.get("name"))
        # "Artist @ Venue" -> "Artist"
        if venue_name and title.endswith(" @ " + venue_name):
            title = title[: -len(" @ " + venue_name)]
        title = re.sub(r"\s+@\s+[^@]+$", "", title) or title
        schedule = {"start_datetime_utc": _utc(dt.datetime.combine(first, when.time()), city["timezone"]),
                    "local_date": first.isoformat(), "is_date_range": end > first}
        if "T" in start and first == day:
            schedule["local_time"] = when.strftime("%H:%M")
        if end > first:
            schedule["end_date"] = end.isoformat()
        event = {
            "id": "sk:" + url.rstrip("/").rsplit("/", 1)[-1].split("-")[0],
            "title": title, "category": {"id": "CONCERT"}, "city": city_dto(city), "schedule": schedule,
            "ticket_offers": [{"provider": SOURCE, "url": url, "offer_type": "PRIMARY"}],
            "source_attribution": {"primary_source": SOURCE, "last_synced_at": synced_at},
            "status": "SCHEDULED",
        }
        sub = genre(item, url)
        if sub:
            event["subcategory"] = sub
        if venue_name:
            venue = {"id": "sk:" + re.sub(r"[^a-z0-9]+", "-", venue_name.lower()).strip("-"), "name": venue_name,
                     "city": city_dto(city)}
            street = clean((location.get("address") or {}).get("streetAddress"))
            if street:
                venue["address"] = street
            geo = location.get("geo") or {}
            try:
                venue["coordinates"] = {"lat": float(geo["latitude"]), "lng": float(geo["longitude"])}
            except (KeyError, TypeError, ValueError):
                pass
            event["venue"] = venue
        image = _https(item.get("image") if isinstance(item.get("image"), str) else None)
        if image:
            event["media"] = {"image_url": image, "attribution": SOURCE}
        out.append(event)
    return out


def collect(cities: list[dict], city_dto, synced_at: str, today: dt.date | None = None, log=print) -> dict[str, list[dict]]:
    today = today or dt.date.today()
    by_id = {c["id"]: c for c in cities}
    result: dict[str, list[dict]] = {}
    for city_id, metro in METROS.items():
        city = by_id.get(city_id)
        if city is None:
            continue
        events: dict[str, dict] = {}
        for number in range(1, PAGES + 1):
            page = webfetch.get(f"{BASE}/metro-areas/{metro}" + (f"?page={number}" if number > 1 else ""))
            found = concerts(page, city, city_dto, synced_at, today) if page else []
            for event in found:
                events.setdefault(event["id"], event)
            if not page or "MusicEvent" not in page:   # past the last page
                break
        log(f"  songkick {city_id}: {len(events)} concerts")
        if events:
            result[city_id] = list(events.values())
    return result
