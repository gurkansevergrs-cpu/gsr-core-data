"""Theatre, opera and ballet in Berlin, Munich and Hamburg from the theatres' own programme pages.

Two kinds of public, machine-readable programme data are used, only where robots.txt allows:
- schema.org Event data (JSON-LD) on the programme page: Maxim Gorki Theater and berlin.de/tickets
  (Berlin), Ohnsorg-Theater (Hamburg);
- the per-performance iCalendar (.ics) "add to calendar" files linked from the programme page:
  Komische Oper Berlin, Münchner Kammerspiele, Residenztheater (Munich), Hamburgische Staatsoper
  and Hamburg Ballett. The production page's og:image is used as the poster.

Only real performances with a date are published; guided tours, workshops and talks are skipped.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import html
import re
from urllib.parse import urljoin, urlsplit

import webfetch
from biletinial import clean, _https

HORIZON_DAYS = 90
ICS_PER_SOURCE = 90
IMAGES_PER_SOURCE = 40

# (city id, programme page, provider shown in the app, default subcategory)
JSONLD_SOURCES = [
    ("de-berlin", "https://www.gorki.de/de/spielplan", "Maxim Gorki Theater", "THEATRE"),
    ("de-berlin", "https://www.berlin.de/tickets/theater/", "berlin.de", "THEATRE"),
    ("de-berlin", "https://www.berlin.de/tickets/oper/", "berlin.de", "OPERA"),
    ("de-berlin", "https://www.berlin.de/tickets/musical/", "berlin.de", "MUSICAL"),
    ("de-hamburg", "https://www.ohnsorg.de/spielplan/", "Ohnsorg-Theater", "THEATRE"),
]
ICS_SOURCES = [
    ("de-berlin", "https://www.komische-oper-berlin.de/spielplan/", "Komische Oper Berlin", "OPERA"),
    ("de-munich", "https://www.muenchner-kammerspiele.de/de/programm/", "Münchner Kammerspiele", "THEATRE"),
    ("de-munich", "https://www.residenztheater.de/spielplan", "Residenztheater", "THEATRE"),
    ("de-hamburg", "https://www.die-hamburgische-staatsoper.de/de/kalender/oper", "Hamburgische Staatsoper", "OPERA"),
    ("de-hamburg", "https://hamburgballett.die-hamburgische-staatsoper.de/de/kalender/ballett", "Hamburg Ballett", "BALLET"),
]
# Whole words only: "Führung" (guided tour) must not catch "Uraufführung" (premiere).
SKIP = re.compile(r"\b(führung|führungen|fuehrung|workshop|werkstatt|gespräch|gespraech|einführung|einfuehrung|"
                  r"probe|kantine|lesung|party|konzert|sinfoniekonzert|kammerkonzert|kammermusik|liederabend|gutschein)\b")
EVENT_TYPES = ("Event", "TheaterEvent", "MusicEvent", "DanceEvent", "ComedyEvent")


def subcategory(title: str, default: str) -> str:
    low = title.lower()
    if "ballett" in low or "ballet" in low or "tanz" in low:
        return "BALLET"
    if "musical" in low:
        return "MUSICAL"
    if "oper " in low or low.endswith(" oper") or "operette" in low:
        return "OPERA"
    return default


def skipped(title: str) -> bool:
    return bool(SKIP.search(title.lower()))


def _utc_and_local(start: dt.datetime) -> tuple[str, dt.date, str]:
    """start is timezone-aware; returns (UTC instant, Berlin date, Berlin time)."""
    try:
        from zoneinfo import ZoneInfo
        local = start.astimezone(ZoneInfo("Europe/Berlin"))
    except Exception:
        local = start.astimezone(dt.timezone(dt.timedelta(hours=1)))
    return start.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), local.date(), local.strftime("%H:%M")


def _event(city: dict, city_dto, synced_at: str, provider: str, title: str, start: dt.datetime, link: str,
           sub: str, venue: str | None, image: str | None) -> dict:
    utc, day, time = _utc_and_local(start)
    event = {
        "id": "de:" + hashlib.sha1(f"{link}|{utc}|{title}".encode()).hexdigest()[:16],
        "title": title, "category": {"id": "THEATRE_MUSICAL"}, "subcategory": sub, "city": city_dto(city),
        "schedule": {"start_datetime_utc": utc, "local_date": day.isoformat(), "local_time": time, "is_date_range": False},
        "ticket_offers": [{"provider": provider, "url": link, "offer_type": "PRIMARY"}],
        "source_attribution": {"primary_source": provider, "last_synced_at": synced_at},
        "status": "SCHEDULED",
    }
    if venue:
        event["venue"] = {"id": "de:" + re.sub(r"[^a-z0-9]+", "-", venue.lower()).strip("-"), "name": venue, "city": city_dto(city)}
    if image:
        event["media"] = {"image_url": image, "attribution": provider}
    return event


def _in_window(day: dt.date, today: dt.date) -> bool:
    return today <= day <= today + dt.timedelta(days=HORIZON_DAYS)


# ---------- JSON-LD programme pages ----------

def _walk(node, out):
    if isinstance(node, list):
        for n in node:
            _walk(n, out)
    elif isinstance(node, dict):
        if node.get("@type") in EVENT_TYPES:
            out.append(node)
        for key in ("@graph", "itemListElement", "item", "subEvent"):
            if key in node:
                _walk(node[key], out)


def jsonld_events(page: str, city: dict, city_dto, synced_at: str, today: dt.date, provider: str,
                  default_sub: str, page_url: str) -> list[dict]:
    items: list = []
    _walk(webfetch.json_ld(page), items)
    host = urlsplit(page_url).netloc
    out = []
    for item in items:
        title = clean(item.get("name"))
        offers = item.get("offers")
        link = _https(item.get("url")) or (_https(offers.get("url")) if isinstance(offers, dict) else None)
        if not title or not link or urlsplit(link).netloc != host or skipped(title):
            continue
        if "Cancelled" in str(item.get("eventStatus")) or "Postponed" in str(item.get("eventStatus")):
            continue
        try:
            start = dt.datetime.fromisoformat(str(item.get("startDate")).replace("Z", "+00:00").replace(".000000", ""))
        except ValueError:
            continue
        if start.tzinfo is None:
            continue
        if not _in_window(_utc_and_local(start)[1], today):
            continue
        image = item.get("image")
        image = _https(image[0] if isinstance(image, list) and image else image if isinstance(image, str) else None)
        if image:
            image = image.split("?prefix=")[0] if "prefix=staging" in image else image
        venue = clean((item.get("location") or {}).get("name")) if isinstance(item.get("location"), dict) else None
        out.append(_event(city, city_dto, synced_at, provider, title, start, link,
                          subcategory(title, default_sub), venue or provider, image))
    return out


# ---------- iCalendar programme pages ----------

def ics_links(page: str, page_url: str) -> list[str]:
    host = urlsplit(page_url).netloc
    links = []
    for raw in re.findall(r'href="([^"]+\.ics[^"]*)"', page):
        url = urljoin(page_url, html.unescape(raw))
        if urlsplit(url).netloc == host and url.startswith("https://") and url not in links:
            links.append(url)
    return links


def _unfold(text: str) -> list[str]:
    return re.sub(r"\r?\n[ \t]", "", text).splitlines()


def parse_ics(text: str) -> list[dict]:
    """VEVENTs as dicts: summary, start (aware datetime), location, description, url."""
    events, current = [], None
    for line in _unfold(text):
        if line == "BEGIN:VEVENT":
            current = {}
        elif line == "END:VEVENT":
            if current is not None and current.get("start") and current.get("summary"):
                events.append(current)
            current = None
        elif current is not None and ":" in line:
            key, value = line.split(":", 1)
            name, *params = key.split(";")
            value = value.replace("\\,", ",").replace("\\;", ";").replace("\\n", "\n").strip()
            if name == "SUMMARY":
                current["summary"] = value
            elif name == "LOCATION":
                current["location"] = value
            elif name == "DESCRIPTION":
                current["description"] = value
            elif name == "URL":
                current["url"] = value
            elif name == "DTSTART":
                tz = next((p.split("=", 1)[1] for p in params if p.startswith("TZID=")), None)
                try:
                    if value.endswith("Z"):
                        start = dt.datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=dt.timezone.utc)
                    else:
                        naive = dt.datetime.strptime(value[:15], "%Y%m%dT%H%M%S")
                        try:
                            from zoneinfo import ZoneInfo
                            start = naive.replace(tzinfo=ZoneInfo(tz or "Europe/Berlin"))
                        except Exception:
                            start = naive.replace(tzinfo=dt.timezone(dt.timedelta(hours=1)))
                    current["start"] = start
                except ValueError:
                    pass
    return events


def production_link(ics_url: str, vevent: dict, page_url: str) -> str:
    """The play's own page: the ICS URL field, an https link in the description, or the .ics path's parent."""
    host = urlsplit(page_url).netloc
    for candidate in (vevent.get("url"), *re.findall(r"https://[^\s]+", vevent.get("description", ""))):
        url = _https(candidate)
        if url and urlsplit(url).netloc == host:
            return url
    path = urlsplit(ics_url).path
    if path.endswith(".ics") and "/programm/" in path:
        return urljoin(ics_url, path.rsplit("/", 1)[0])
    return page_url


def og_image(page: str | None) -> str | None:
    if not page:
        return None
    match = re.search(r'<meta[^>]+property="og:image"[^>]+content="([^"]+)"', page) or \
        re.search(r'<meta[^>]+content="([^"]+)"[^>]+property="og:image"', page)
    return _https(html.unescape(match.group(1))) if match else None


def ics_events(page: str, city: dict, city_dto, synced_at: str, today: dt.date, provider: str, default_sub: str,
               page_url: str, fetch=webfetch.get) -> list[dict]:
    out, images = [], {}
    for ics_url in ics_links(page, page_url)[:ICS_PER_SOURCE]:
        text = fetch(ics_url)
        if not text or "BEGIN:VEVENT" not in text:
            continue
        for vevent in parse_ics(text):
            title = clean(re.sub(r"^[^:]{3,40}:\s+", "", vevent["summary"]) if vevent["summary"].lower().startswith(
                ("kammerspiele", "residenztheater", "staatsoper")) else vevent["summary"])
            if not title or skipped(title) or not _in_window(_utc_and_local(vevent["start"])[1], today):
                continue
            link = production_link(ics_url, vevent, page_url)
            if link not in images and len(images) < IMAGES_PER_SOURCE and link != page_url:
                images[link] = og_image(fetch(link))
            out.append(_event(city, city_dto, synced_at, provider, title, vevent["start"], link,
                              subcategory(title, default_sub), clean(vevent.get("location")) or provider, images.get(link)))
    return out


def collect(cities: list[dict], city_dto, synced_at: str, today: dt.date | None = None, log=print) -> dict[str, list[dict]]:
    today = today or dt.date.today()
    by_id = {c["id"]: c for c in cities}
    result: dict[str, dict[str, dict]] = {}
    for city_id, url, provider, sub in JSONLD_SOURCES + ICS_SOURCES:
        city = by_id.get(city_id)
        page = webfetch.get(url) if city else None
        if not page:
            log(f"  germany {provider}: no page")
            continue
        parse = jsonld_events if (city_id, url, provider, sub) in JSONLD_SOURCES else ics_events
        found = parse(page, city, city_dto, synced_at, today, provider, sub, url)
        for event in found:
            result.setdefault(city_id, {})[event["id"]] = event
        log(f"  germany {provider}: {len(found)} performances")
    return {city_id: list(events.values()) for city_id, events in result.items()}
