"""European stages (Berlin, Munich, Hamburg, Vienna, Amsterdam, Paris, Zurich) from the venues' own pages.

Also Amsterdam exhibitions from the city's I amsterdam agenda.

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
    ("ch-zurich", "https://www.opernhaus.ch/spielplan/kalendarium/", "Opernhaus Zürich", "OPERA"),
    ("fr-paris", "https://www.theatredelaville-paris.com/fr/spectacles/saison-26-27/theatre", "Théâtre de la Ville", "THEATRE"),
    ("fr-paris", "https://www.theatredelaville-paris.com/fr/spectacles/saison-26-27/danse", "Théâtre de la Ville", "BALLET"),
]
ICS_SOURCES = [
    ("de-berlin", "https://www.komische-oper-berlin.de/spielplan/", "Komische Oper Berlin", "OPERA"),
    ("de-munich", "https://www.muenchner-kammerspiele.de/de/programm/", "Münchner Kammerspiele", "THEATRE"),
    ("de-munich", "https://www.residenztheater.de/spielplan", "Residenztheater", "THEATRE"),
    ("de-hamburg", "https://www.die-hamburgische-staatsoper.de/de/kalender/oper", "Hamburgische Staatsoper", "OPERA"),
    ("de-hamburg", "https://hamburgballett.die-hamburgische-staatsoper.de/de/kalender/ballett", "Hamburg Ballett", "BALLET"),
    ("ch-zurich", "https://www.schauspielhaus.ch/de/spielplan/", "Schauspielhaus Zürich", "THEATRE"),
]
# Listing page whose production pages carry the JSON-LD: (city, listing, production-link regex, provider,
# default subcategory, category).
DETAIL_SOURCES = [
    ("at-vienna", "https://www.volksoper.at/spielplan", r"volksoper\.at/produktion/", "Volksoper Wien", "OPERA", "THEATRE_MUSICAL"),
    ("nl-amsterdam", "https://ita.nl/nl/agenda-stadsschouwburg", r"ita\.nl/nl/voorstellingen/", "Internationaal Theater Amsterdam",
     "THEATRE", "THEATRE_MUSICAL"),
    ("fr-paris", "https://www.chatelet.com/programmation/", r"chatelet\.com/programmation/\d\d-\d\d/", "Théâtre du Châtelet",
     "MUSICAL", "THEATRE_MUSICAL"),
    ("nl-amsterdam", "https://www.iamsterdam.com/uit/agenda/tentoonstellingen",
     r"iamsterdam\.com/uit/agenda/tentoonstellingen/[^/]+/[^/?#]+$", "I amsterdam", "ART", "EXHIBITION"),
]
DETAIL_SOURCES += [
    ("fr-paris", "https://www.theatre-odeon.eu/fr", r"theatre-odeon\.eu/fr/saison-\d{4}-\d{4}/spectacles-", "Odéon-Théâtre de l'Europe",
     "THEATRE", "THEATRE_MUSICAL"),
    ("be-brussels", "https://www.lamonnaiedemunt.be/fr/calendar", r"lamonnaiedemunt\.be/fr/program/\d+-", "La Monnaie / De Munt",
     "OPERA", "THEATRE_MUSICAL"),
]
DETAIL_PAGES = 40
# Programme pages without event data whose performance links carry the date (and often the time):
# (city, listing, link regex with y m d [H M] groups and a 'slug' group, provider, default subcategory).
URL_DATE_SOURCES = [
    ("at-vienna", "https://www.burgtheater.at/spielplan",
     r"/veranstaltungen/(?P<slug>[a-z0-9-]+)/(?P<y>20\d\d)-(?P<m>\d\d)-(?P<d>\d\d)$", "Burgtheater", "THEATRE"),
    ("hu-budapest", "https://www.opera.hu/en/programme/",
     r"/programme/20\d\d-20\d\d/(?P<slug>[^/]+)/[a-z-]+-(?P<y>20\d\d)-(?P<m>\d\d)-(?P<d>\d\d)-(?P<H>\d\d)(?P<M>\d\d)/$",
     "Hungarian State Opera", "OPERA"),
    ("cz-prague", "https://www.narodni-divadlo.cz/en/programme",
     r"/en/show/(?P<slug>[^?]+)\?t=(?P<y>20\d\d)-(?P<m>\d\d)-(?P<d>\d\d)-(?P<H>\d\d)-(?P<M>\d\d)$",
     "National Theatre Prague", "THEATRE"),
]
URL_DATE_LINKS = 150
# Whole words only: "Führung" (guided tour) must not catch "Uraufführung" (premiere).
SKIP = re.compile(r"\b(führung|führungen|fuehrung|workshop|werkstatt|gespräch|gespraech|einführung|einfuehrung|"
                  r"probe|kantine|lesung|party|konzert|sinfoniekonzert|kammerkonzert|kammermusik|liederabend|gutschein|"
                  r"rondleiding|visite|visites|guided tour|tour guidé)\b")
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
           sub: str, venue: str | None, image: str | None, category: str = "THEATRE_MUSICAL",
           end: dt.date | None = None) -> dict:
    utc, day, time = _utc_and_local(start)
    schedule = {"start_datetime_utc": utc, "local_date": day.isoformat(), "local_time": time, "is_date_range": False}
    if end and end > day:   # exhibitions: open across their run, no single show time
        schedule = {"start_datetime_utc": utc, "local_date": day.isoformat(), "is_date_range": True, "end_date": end.isoformat()}
    event = {
        "id": "de:" + hashlib.sha1(f"{link}|{utc}|{title}".encode()).hexdigest()[:16],
        "title": title, "category": {"id": category}, "subcategory": sub, "city": city_dto(city),
        "schedule": schedule,
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


def _aware(value) -> dt.datetime | None:
    try:
        start = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00").replace(".000000", ""))
    except ValueError:
        return None
    if start.tzinfo is None:   # local time without offset (ITA, Opernhaus): the venue's own time zone
        try:
            from zoneinfo import ZoneInfo
            start = start.replace(tzinfo=ZoneInfo("Europe/Berlin"))
        except Exception:
            start = start.replace(tzinfo=dt.timezone(dt.timedelta(hours=1)))
    return start


def jsonld_events(page: str, city: dict, city_dto, synced_at: str, today: dt.date, provider: str,
                  default_sub: str, page_url: str, category: str = "THEATRE_MUSICAL") -> list[dict]:
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
        start = _aware(item.get("startDate"))
        if start is None:
            continue
        end = None
        if category == "EXHIBITION":
            finish = _aware(item.get("endDate") or item.get("startDate"))
            end = _utc_and_local(finish)[1] if finish else None
            if end is None or end < today or _utc_and_local(start)[1] > today + dt.timedelta(days=HORIZON_DAYS):
                continue
            if _utc_and_local(start)[1] < today:   # running now: show it from today
                start = dt.datetime.combine(today, dt.time(10, 0), tzinfo=start.tzinfo)
        elif not _in_window(_utc_and_local(start)[1], today):
            continue
        image = item.get("image")
        image = _https(image[0] if isinstance(image, list) and image else image if isinstance(image, str) else None)
        if image:
            image = image.split("?prefix=")[0] if "prefix=staging" in image else image
        venue = clean((item.get("location") or {}).get("name")) if isinstance(item.get("location"), dict) else None
        sub = default_sub if category == "EXHIBITION" else subcategory(title, default_sub)
        out.append(_event(city, city_dto, synced_at, provider, title, start, link, sub, venue or provider, image,
                          category, end))
    return out


def detail_events(page: str, city: dict, city_dto, synced_at: str, today: dt.date, provider: str, default_sub: str,
                  page_url: str, pattern: str, category: str, fetch=webfetch.get) -> list[dict]:
    """Follow the listing's production links and read each production page's JSON-LD."""
    host = urlsplit(page_url).netloc.removeprefix("www.")
    links = []
    for raw in re.findall(r'href="([^"#]+)"', page):
        url = urljoin(page_url, html.unescape(raw))
        if re.search(pattern, url) and urlsplit(url).netloc.removeprefix("www.") == host and url not in links:
            links.append(url)
    out = []
    for url in links[:DETAIL_PAGES]:
        detail = fetch(url)
        if not detail:
            continue
        found = jsonld_events(detail, city, city_dto, synced_at, today, provider, default_sub, url, category)
        if found and not any("media" in e for e in found):
            image = og_image(detail, url)
            for e in found:
                if image:
                    e["media"] = {"image_url": image, "attribution": provider}
        out.extend(found)
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
    events, current, nested = [], None, 0
    for line in _unfold(text):
        if line.startswith("BEGIN:") and line != "BEGIN:VEVENT" and current is not None:
            nested += 1          # VALARM etc.: its DESCRIPTION ("Reminder") is not the play's
        elif line.startswith("END:") and line != "END:VEVENT" and nested:
            nested -= 1
        elif nested:
            continue
        elif line == "BEGIN:VEVENT":
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


def og_image(page: str | None, base: str = "") -> str | None:
    if not page:
        return None
    match = re.search(r'<meta[^>]+property="og:image"[^>]+content="([^"]+)"', page) or \
        re.search(r'<meta[^>]+content="([^"]+)"[^>]+property="og:image"', page)
    url = _https(urljoin(base, html.unescape(match.group(1)))) if match else None
    return url.replace("?w=2000", "?w=800") if url else None   # Kammerspiele serves press photos at 2000 px


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
                images[link] = og_image(fetch(link), link)
            out.append(_event(city, city_dto, synced_at, provider, title, vevent["start"], link,
                              subcategory(title, default_sub), clean(vevent.get("location")) or provider, images.get(link)))
    return out


def og_title(page: str | None) -> str | None:
    if not page:
        return None
    match = re.search(r'<meta[^>]+property="og:title"[^>]+content="([^"]+)"', page) or \
        re.search(r'<meta[^>]+content="([^"]+)"[^>]+property="og:title"', page)
    if not match:
        return None
    title = clean(match.group(1))
    return re.split(r"\s+[|–—-]\s+(?=[^|–—-]*$)", title)[0].strip() or None   # drop " | Site name"


def url_date_events(page: str, city: dict, city_dto, synced_at: str, today: dt.date, provider: str, default_sub: str,
                    page_url: str, pattern: str, fetch=webfetch.get) -> list[dict]:
    """Performances from dated links; one production page per title gives the name and poster."""
    host = urlsplit(page_url).netloc.removeprefix("www.")
    seen, out, productions = set(), [], {}
    for raw in re.findall(r'href="([^"#]+)"', page):
        url = urljoin(page_url, html.unescape(raw))
        if urlsplit(url).netloc.removeprefix("www.") != host or url in seen:
            continue
        match = re.search(pattern, url)
        if not match:
            continue
        seen.add(url)
        if len(seen) > URL_DATE_LINKS:
            break
        parts = match.groupdict()
        try:
            naive = dt.datetime(int(parts["y"]), int(parts["m"]), int(parts["d"]), int(parts.get("H") or 19), int(parts.get("M") or 0))
        except (TypeError, ValueError):
            continue
        if not _in_window(naive.date(), today):
            continue
        slug = parts["slug"]
        if slug not in productions:
            detail = fetch(url)
            productions[slug] = (og_title(detail), og_image(detail, url))
        title, image = productions[slug]
        if not title or skipped(title):
            continue
        start = _aware(naive.isoformat())
        event = _event(city, city_dto, synced_at, provider, title, start, url, subcategory(title, default_sub), provider, image)
        if not parts.get("H"):
            event["schedule"].pop("local_time", None)   # the link gives the day only
        out.append(event)
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
        log(f"  stages {provider}: {len(found)} performances")
    for city_id, url, pattern, provider, sub, category in DETAIL_SOURCES:
        city = by_id.get(city_id)
        page = webfetch.get(url) if city else None
        if not page:
            log(f"  stages {provider}: no page")
            continue
        found = detail_events(page, city, city_dto, synced_at, today, provider, sub, url, pattern, category)
        for event in found:
            result.setdefault(city_id, {})[event["id"]] = event
        log(f"  stages {provider}: {len(found)} dates")
    for city_id, url, pattern, provider, sub in URL_DATE_SOURCES:
        city = by_id.get(city_id)
        page = webfetch.get(url) if city else None
        if not page:
            log(f"  stages {provider}: no page")
            continue
        found = url_date_events(page, city, city_dto, synced_at, today, provider, sub, url, pattern)
        for event in found:
            result.setdefault(city_id, {})[event["id"]] = event
        log(f"  stages {provider}: {len(found)} performances")
    return {city_id: list(events.values()) for city_id, events in result.items()}
