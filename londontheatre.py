"""London stage shows from London Theatre (londontheatre.co.uk).

Ticketmaster needs an API key, so London is filled without one from the public
"What's On" pages, which carry schema.org TheaterEvent data (JSON-LD) for every
listed show: name, poster image, venue, run dates and the ticket page URL.

robots.txt is checked before every request and requests are spaced out.
Standard library only.
"""
from __future__ import annotations

import datetime as dt
import http.client
import html
import json
import re
import sys
import time
import urllib.error
import urllib.request
import urllib.robotparser

BASE = "https://www.londontheatre.co.uk"
SOURCE = "London Theatre"
CITY_ID = "gb-london"
USER_AGENT = "gsr-core-data/1.0 (event listing; links back to the ticket page)"
# What's On page -> app subcategory (the first page a show appears on wins, so specific pages come first).
PAGES = [("whats-on/opera", "OPERA"), ("whats-on/ballet", "BALLET"), ("whats-on/dance", "BALLET"),
         ("whats-on/musicals", "MUSICAL"), ("whats-on/drama", "THEATRE"), ("whats-on/shakespeare", "THEATRE"),
         ("whats-on/opening-soon", None), ("whats-on/west-end", None), ("whats-on", None)]
DELAY = 1.0
HORIZON_DAYS = 90

_robots: urllib.robotparser.RobotFileParser | None = None


def clean(text) -> str:
    return re.sub(r"\s+", " ", html.unescape(str(text or "")).replace("\xa0", " ")).strip()


def allowed(url: str) -> bool:
    global _robots
    if _robots is None:
        _robots = urllib.robotparser.RobotFileParser(BASE + "/robots.txt")
        try:
            _robots.read()
        except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException):  # incl. ssl errors, resets
            _robots = urllib.robotparser.RobotFileParser()
            _robots.parse(["User-agent: *", "Disallow: /"])
    return _robots.can_fetch(USER_AGENT, url)


def get(url: str) -> str | None:
    if not allowed(url):
        print(f"  robots.txt disallows {url}", file=sys.stderr)
        return None
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = resp.read().decode("utf-8", "replace")
            time.sleep(DELAY)
            return body
        except urllib.error.HTTPError as err:
            time.sleep(DELAY)
            if err.code in (429, 500, 502, 503, 504) and attempt < 2:
                time.sleep(5 * (attempt + 1))
                continue
            return None
        except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException):  # incl. ssl errors, resets
            time.sleep(3 * (attempt + 1))
    return None


def json_ld(page: str) -> list:
    out = []
    for block in re.findall(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', page, re.S):
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            continue
        out.extend(data if isinstance(data, list) else [data])
    return out


def _https(url) -> str | None:
    if isinstance(url, list):
        url = url[0] if url else None
    if not isinstance(url, str):
        return None
    url = html.unescape(url).strip().replace(" ", "%20")
    if url.startswith("https://") and "@" not in url.split("/")[2]:
        return url
    return None


def _date(value) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


EXHIBIT_WORDS = ("exhibition", "museum", "gallery", "art fair", "frieze", "immersive")
SKIP_WORDS = ("comedy", "stand-up", "stand up", "magic mike", "drag brunch")


def classify(title: str, default: str | None) -> str:
    low = title.lower()
    if default:
        return default
    if "opera" in low:
        return "OPERA"
    if "ballet" in low or "nutcracker" in low or "swan lake" in low:
        return "BALLET"
    if "musical" in low:
        return "MUSICAL"
    return "THEATRE"


def shows(page: str, default_sub: str | None, city: dict, city_dto, synced_at: str, today: dt.date,
          base: str = BASE, source: str = SOURCE, prefix: str = "lt") -> list[dict]:
    """App events for the shows listed on one What's On page that run in the next HORIZON_DAYS days.
    Also used for TodayTix pages (same publisher, same page data)."""
    out = []
    horizon = today + dt.timedelta(days=HORIZON_DAYS)
    for item in json_ld(page):
        if item.get("@type") not in ("TheaterEvent", "Event", "MusicEvent", "DanceEvent"):
            continue
        title, url = clean(item.get("name")), _https(item.get("url"))
        start = _date(item.get("startDate"))
        if not title or not url or not url.startswith(base + "/") or start is None:
            continue
        low = title.lower()
        if any(word in low for word in SKIP_WORDS):
            continue
        exhibition = any(word in low for word in EXHIBIT_WORDS)
        end = _date(item.get("endDate"))
        if "Cancelled" in str(item.get("eventStatus")) or (end and end < today) or start > horizon:
            continue
        first = max(start, today)
        last = min(end, horizon) if end else horizon   # open-ended runs: show them across the whole window
        slug = url.rstrip("/").rsplit("/", 1)[-1]
        event = {
            "id": f"{prefix}:{slug}",
            "title": title, "category": {"id": "EXHIBITION" if exhibition else "THEATRE_MUSICAL"},
            "subcategory": "ART" if exhibition else classify(title, default_sub),
            "city": city_dto(city),
            "schedule": {"start_datetime_utc": f"{first.isoformat()}T19:30:00Z", "local_date": first.isoformat(),
                         "is_date_range": first != last, "end_date": last.isoformat()},
            "ticket_offers": [{"provider": source, "url": url, "offer_type": "PRIMARY"}],
            "source_attribution": {"primary_source": source, "last_synced_at": synced_at},
            "status": "SCHEDULED",
        }
        offer = item.get("offers") or {}
        if isinstance(offer, list):
            offer = offer[0] if offer else {}
        try:
            price, currency = float(offer.get("price")), str(offer.get("priceCurrency"))
            if price > 0 and re.fullmatch(r"[A-Z]{3}", currency):
                event["ticket_offers"][0]["price_range"] = {"min": price, "max": price, "currency": currency}
        except (TypeError, ValueError):
            pass
        venue = clean((item.get("location") or {}).get("name"))
        if venue:
            event["venue"] = {"id": f"{prefix}:" + re.sub(r"[^a-z0-9]+", "-", venue.lower()).strip("-"), "name": venue,
                              "city": city_dto(city)}
        image = _https(item.get("image"))
        if image:
            event["media"] = {"image_url": image, "attribution": source}
        out.append(event)
    return out


def collect(cities: list[dict], city_dto, synced_at: str, today: dt.date | None = None, log=print) -> dict[str, list[dict]]:
    today = today or dt.date.today()
    city = next((c for c in cities if c["id"] == CITY_ID), None)
    if city is None:
        return {}
    events: dict[str, dict] = {}
    for path, sub in PAGES:
        page = get(f"{BASE}/{path}")
        for event in shows(page, sub, city, city_dto, synced_at, today) if page else []:
            events.setdefault(event["id"], event)
        log(f"  londontheatre {path}: {len(events)} shows so far")
    return {CITY_ID: list(events.values())} if events else {}
