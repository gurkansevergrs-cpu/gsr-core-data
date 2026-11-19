"""Turkish events from Biletinial (biletinial.com).

Ticketmaster does not cover Turkey, so Turkish cities are filled from the
public Biletinial city pages, for example /tr-tr/tiyatro/ankara. Each listed
play page carries schema.org Event data (JSON-LD) with one entry per
performance: name, poster image, date and time, hall, province and ticket URL.
Biletinial also sells the State Theatres' (Devlet Tiyatroları) tickets.

robots.txt is checked before every request, requests are spaced out, and only
public listing and event pages are read. Standard library only.
"""
from __future__ import annotations

import datetime as dt
import html
import json
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.request
import urllib.robotparser

BASE = "https://biletinial.com"
SOURCE = "Biletinial"
USER_AGENT = "gsr-core-data/1.0 (event listing; links back to the ticket page)"
# Biletinial list page -> app category and default subcategory.
LISTS = {"tiyatro": ("THEATRE_MUSICAL", "THEATRE"), "opera-bale": ("THEATRE_MUSICAL", None), "muzik": ("CONCERT", None),
         "etkinlik": ("EXHIBITION", "ART")}  # "etkinlik" is mixed: only exhibitions are kept
SKIP_WORDS = ("stand up", "stand-up", "standup", "talk show", "sinema")
EXHIBIT_WORDS = ("sergi", "muze", "exhibition", "exhibit", "galeri", "gallery", "lumina", "immersive", "bienal", "biennial")
DELAY = 1.0
HORIZON_DAYS = 90

_robots: urllib.robotparser.RobotFileParser | None = None


def fold(text: str) -> str:
    """Lower-case ASCII form used to match Turkish names (İ, ı, ş, ğ, ç, ö, ü)."""
    text = (text or "").replace("İ", "i").replace("I", "ı").lower().replace("ı", "i")
    return "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch)).strip()


def clean(text) -> str:
    return re.sub(r"\s+", " ", html.unescape(str(text or "")).replace("\xa0", " ")).strip()


def classify(list_name: str, title: str) -> tuple[str, str | None] | None:
    low = fold(title)
    if any(word in low for word in SKIP_WORDS):
        return None
    category, sub = LISTS[list_name]
    if list_name == "etkinlik":
        if not any(w in low for w in EXHIBIT_WORDS):
            return None
        if "fotograf" in low or "photo" in low:
            return category, "PHOTOGRAPHY"
        if "heykel" in low or "sculpt" in low:
            return category, "SCULPTURE"
        if any(w in low for w in ("dijital", "digital", "teknoloji", "immersive", "lumina", "sanal gerceklik", " vr")):
            return category, "TECHNOLOGY"
        if any(w in low for w in ("tarih", "history", "antik", "arkeoloji", "osmanli")):
            return category, "HISTORY"
        if "tasarim" in low or "design" in low:
            return category, "DESIGN"
        return category, sub
    if list_name == "tiyatro":
        return ("THEATRE_MUSICAL", "MUSICAL") if "muzikal" in low or "musical" in low else (category, sub)
    if list_name == "opera-bale":
        if "bale" in low or "ballet" in low or "dans" in low:
            return "THEATRE_MUSICAL", "BALLET"
        return "THEATRE_MUSICAL", "OPERA"
    if "festival" in low or "fest " in low:
        return "CONCERT", "FESTIVAL"
    if any(w in low for w in ("caz", "jazz", "blues")):
        return "CONCERT", "JAZZ"
    if any(w in low for w in ("senfoni", "orkestra", "filarmoni", "resital", "klasik", "oda muzigi", "piyano", "keman",
                              "cello", "viyolonsel", "koro", "chopin", "mozart", "beethoven", "bach", "vivaldi", "candlelight")):
        return "CONCERT", "CLASSICAL"
    if any(w in low for w in ("rock", "metal", "punk", "grunge")):
        return "CONCERT", "ROCK"
    if any(w in low for w in ("pop", "tribute", "hits", "90'lar", "80'ler", "2000'ler", "90lar", "80ler")):
        return "CONCERT", "POP"
    if any(w in low for w in ("dj ", "dj:", "techno", "house", "elektronik", "electronic", "party", "parti")):
        return "CONCERT", "ELECTRONIC"
    return "CONCERT", None


def allowed(url: str) -> bool:
    global _robots
    if _robots is None:
        _robots = urllib.robotparser.RobotFileParser(BASE + "/robots.txt")
        try:
            _robots.read()
        except (urllib.error.URLError, TimeoutError):
            _robots = urllib.robotparser.RobotFileParser()
            _robots.parse(["User-agent: *", "Disallow: /"])  # cannot read the rules: read nothing
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
        except (urllib.error.URLError, TimeoutError):
            time.sleep(3 * (attempt + 1))
    return None


def json_ld(page: str) -> list:
    out = []
    for block in re.findall(r'<script type="application/ld\+json">(.*?)</script>', page, re.S):
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            continue
        out.extend(data if isinstance(data, list) else [data])
    return out


def list_urls(page: str) -> list[str]:
    urls = []
    for item in json_ld(page):
        if item.get("@type") == "ItemList":
            for element in item.get("itemListElement") or []:
                url = element.get("url")
                if isinstance(url, str) and url.startswith(BASE + "/tr-tr/") and url not in urls:
                    urls.append(url)
    return urls


def _https(url) -> str | None:
    if not isinstance(url, str):
        return None
    url = html.unescape(url).replace("\xa0", "%20").replace(" ", "%20")
    if url.startswith("https://") and "@" not in url.split("/")[2]:
        return url
    return None


def performances(page: str, list_name: str, cities_by_name: dict, city_dto, synced_at: str, today: dt.date) -> list[tuple[str, dict]]:
    """(city_id, app event) for every upcoming performance on one Biletinial event page."""
    out = []
    for item in json_ld(page):
        if item.get("@type") != "Event":
            continue
        title, url = clean(item.get("name")), _https(item.get("url"))
        start = item.get("startDate")
        if not title or not url or not isinstance(start, str):
            continue
        category = classify(list_name, title)
        if category is None:
            continue
        try:
            when = dt.datetime.fromisoformat(start)
        except ValueError:
            continue
        if when.tzinfo is None:
            continue
        day = when.date()
        if day < today or day > today + dt.timedelta(days=HORIZON_DAYS):
            continue
        location = item.get("location") or {}
        address = location.get("address") or {}
        city = None
        for key in ("addressRegion", "addressLocality"):
            name = fold(clean(address.get(key)))
            # Istanbul comes as "İstanbul Anadolu" / "İstanbul Avrupa": fall back to the first word.
            city = cities_by_name.get(name) or cities_by_name.get(name.split(" ")[0] if name else "")
            if city:
                break
        if city is None:
            continue
        status = "CANCELLED" if "Cancelled" in str(item.get("eventStatus")) else \
            "POSTPONED" if "Postponed" in str(item.get("eventStatus")) or "Rescheduled" in str(item.get("eventStatus")) else "SCHEDULED"
        slug = url.rstrip("/").rsplit("/", 1)[-1]
        event = {
            "id": f"bi:{slug}:{when.strftime('%Y%m%dT%H%M')}:{city['id']}",
            "title": title, "category": {"id": category[0]}, "city": city_dto(city),
            "schedule": {"start_datetime_utc": when.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                         "local_date": day.isoformat(), "local_time": when.strftime("%H:%M"), "is_date_range": False},
            "ticket_offers": [{"provider": SOURCE, "url": url, "offer_type": "PRIMARY"}],
            "source_attribution": {"primary_source": SOURCE, "last_synced_at": synced_at},
            "status": status,
        }
        if category[1]:
            event["subcategory"] = category[1]
        offer = item.get("offers") or {}
        if isinstance(offer, list):
            offer = offer[0] if offer else {}
        try:
            price, currency = float(offer.get("price")), str(offer.get("priceCurrency"))
            if price > 0 and re.fullmatch(r"[A-Z]{3}", currency):
                event["ticket_offers"][0]["price_range"] = {"min": price, "max": price, "currency": currency}
        except (TypeError, ValueError):
            pass
        hall = clean(location.get("name"))
        if hall:
            venue = {"id": f"bi:{fold(hall).replace(' ', '-')}", "name": hall, "city": city_dto(city)}
            street = clean(address.get("streetAddress"))
            if street:
                venue["address"] = street
            geo = location.get("geo") or {}
            try:
                venue["coordinates"] = {"lat": float(geo["latitude"]), "lng": float(geo["longitude"])}
            except (KeyError, TypeError, ValueError):
                pass
            event["venue"] = venue
        image = _https(item.get("image") if isinstance(item.get("image"), str) else (item.get("image") or [None])[0])
        if image:
            event["media"] = {"image_url": image, "attribution": SOURCE}
        out.append((city["id"], event))
    return out


def collect(cities: list[dict], city_dto, synced_at: str, today: dt.date | None = None, log=print) -> dict[str, list[dict]]:
    """Normalized events per city id for every city that has a 'biletinial' slug."""
    today = today or dt.date.today()
    targets = [c for c in cities if c.get("biletinial")]
    by_name = {}
    for c in targets:
        by_name[fold(c["name"]["tr"])] = c
        by_name[fold(c["biletinial"])] = c
    by_name.setdefault("afyon", next((c for c in targets if c["biletinial"] == "afyonkarahisar"), None))
    by_name.setdefault("izmit", next((c for c in targets if c["biletinial"] == "kocaeli"), None))
    by_name.setdefault("antakya", next((c for c in targets if c["biletinial"] == "hatay"), None))
    by_name = {k: v for k, v in by_name.items() if v}

    pages: dict[str, str] = {}  # event URL -> list name it was first found in
    for city in targets:
        for list_name in LISTS:
            page = get(f"{BASE}/tr-tr/{list_name}/{city['biletinial']}")
            found = list_urls(page) if page else []
            for url in found:
                pages.setdefault(url, list_name)
        log(f"  biletinial {city['id']}: {len(pages)} event pages so far")

    result: dict[str, dict[str, dict]] = {}
    for index, (url, list_name) in enumerate(pages.items(), 1):
        page = get(url)
        if not page:
            continue
        for city_id, event in performances(page, list_name, by_name, city_dto, synced_at, today):
            result.setdefault(city_id, {})[event["id"]] = event
        if index % 100 == 0:
            log(f"  biletinial: read {index}/{len(pages)} event pages")
    return {city_id: list(events.values()) for city_id, events in result.items()}
