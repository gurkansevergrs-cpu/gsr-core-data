"""GSR CORE event data builder.

Reads upcoming music and arts events from the Ticketmaster Discovery API
(free key), maps them to the GSR CORE app format and writes static JSON
files that the Android app downloads:

  v1/locations.json                 countries + cities that have events
  v1/events/city-<city_id>.json     all events of one city
  v1/events/country-<CC>.json       all events of one country
  v1/events/global.json             a mixed sample of the soonest events
  v1/artists.json                   artist index
  v1/artists/<artist_id>.json       one artist's events in covered cities
  v1/meta.json                      generation time and counts

Standard library only. The API key is read from the TICKETMASTER_API_KEY
environment variable and is never printed or written to the output.

Usage:
  python ingest.py --out site                      # live (needs the key)
  python ingest.py --out site --fixture f.json     # offline test input
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

API = "https://app.ticketmaster.com/discovery/v2/events.json"
SEGMENTS = "KZFzniwnSyZfZ7v7nJ,KZFzniwnSyZfZ7v7na,KZFzniwnSyZfZ7v7n1"  # Music, Arts & Theatre, Miscellaneous
PAGE_SIZE = 200
MAX_PAGES = 5            # Discovery API deep-paging limit is size * page < 1000
HORIZON_DAYS = 90
CITY_CAP = 1000
COUNTRY_CAP = 1500
GLOBAL_CAP = 400
ARTIST_CAP = 2000
SOURCE = "Ticketmaster"

ROCK = {"rock", "metal", "alternative", "punk", "hard rock", "indie rock"}
POP = {"pop", "r&b", "hip-hop/rap", "country", "latin", "world", "folk", "reggae", "soul"}
CLASSICAL = {"classical", "orchestral", "chamber music", "choral"}
JAZZ = {"jazz", "blues"}
ELECTRONIC = {"dance/electronic", "electronic", "techno", "house"}
SKIP_ARTS = {"comedy", "magic & illusion", "circus & specialty acts", "puppetry", "spectacular", "variety"}


def _norm(value) -> str:
    return (value or "").strip().lower()


def _name(obj) -> str:
    return _norm((obj or {}).get("name"))


def classify(event: dict) -> tuple[str, str | None] | None:
    """Return (category, subcategory) for the app, or None to drop the event."""
    title = _norm(event.get("name"))
    cls = (event.get("classifications") or [{}])[0]
    segment, genre, sub = _name(cls.get("segment")), _name(cls.get("genre")), _name(cls.get("subGenre"))

    if any(word in title for word in ("exhibition", "exhibit ", "museum", "gallery", "sergi", "ausstellung", "exposition")):
        return "EXHIBITION", "HISTORY" if "museum" in title else "ART"
    if segment == "music":
        if "festival" in title or "festival" in genre or "festival" in sub:
            return "CONCERT", "FESTIVAL"
        if "opera" in genre or "opera" in sub:
            return "THEATRE_MUSICAL", "OPERA"
        for group, code in ((ROCK, "ROCK"), (POP, "POP"), (CLASSICAL, "CLASSICAL"), (JAZZ, "JAZZ"), (ELECTRONIC, "ELECTRONIC")):
            if genre in group or sub in group:
                return "CONCERT", code
        return "CONCERT", None
    if segment == "arts & theatre":
        if genre in SKIP_ARTS:
            return None
        if "opera" in genre or "opera" in sub or "opera" in title:
            return "THEATRE_MUSICAL", "OPERA"
        if "ballet" in sub or "ballet" in title or "ballet" in genre:
            return "THEATRE_MUSICAL", "BALLET"
        if "musical" in sub or "musical" in genre or "musical" in title:
            return "THEATRE_MUSICAL", "MUSICAL"
        if genre in {"fine art", "multimedia", "cultural"}:
            return "EXHIBITION", "ART"
        if genre == "classical":
            return "CONCERT", "CLASSICAL"
        if genre in {"theatre", "children's theatre", "dance", "performance art"}:
            return "THEATRE_MUSICAL", "THEATRE" if genre != "dance" else None
        return "THEATRE_MUSICAL", None
    if segment == "miscellaneous" and genre in {"fine art", "multimedia", "cultural"}:
        return "EXHIBITION", "ART"
    return None


def _https(url) -> str | None:
    if isinstance(url, str) and url.startswith("https://") and "@" not in url.split("/")[2]:
        return url
    return None


def _image(images) -> str | None:
    best, best_w = None, -1
    for img in images or []:
        url = _https(img.get("url"))
        width = img.get("width") or 0
        if url and img.get("ratio") == "16_9" and 640 <= width <= 1300 and width > best_w:
            best, best_w = url, width
    if best:
        return best
    for img in images or []:
        if _https(img.get("url")):
            return img["url"]
    return None


def city_dto(city: dict) -> dict:
    return {"id": city["id"], "country_code": city["country_code"], "name": city["name"],
            "timezone": city["timezone"], "slug": city["id"], "latitude": city["lat"], "longitude": city["lng"]}


def _local_time(value) -> str | None:
    if isinstance(value, str) and re.fullmatch(r"\d{2}:\d{2}(:\d{2})?", value):
        return value[:5]
    return None


def normalize(raw: dict, city: dict, synced_at: str) -> dict | None:
    """Map one Ticketmaster event to the GSR CORE event shape, or None."""
    eid, title = raw.get("id"), (raw.get("name") or "").strip()
    url = _https(raw.get("url"))
    if not eid or not title or not url:
        return None
    category = classify(raw)
    if category is None:
        return None
    dates = raw.get("dates") or {}
    start = dates.get("start") or {}
    local_date = start.get("localDate")
    try:
        day = dt.date.fromisoformat(local_date)
    except (TypeError, ValueError):
        return None
    local_time = None if start.get("timeTBA") or start.get("noSpecificTime") else _local_time(start.get("localTime"))
    utc = start.get("dateTime")
    if not (isinstance(utc, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", utc)):
        hour, minute = (int(local_time[:2]), int(local_time[3:5])) if local_time else (0, 0)
        utc = dt.datetime(day.year, day.month, day.day, hour, minute, tzinfo=ZoneInfo(city["timezone"])).astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    status_code = _norm((dates.get("status") or {}).get("code"))
    status = {"cancelled": "CANCELLED", "canceled": "CANCELLED", "postponed": "POSTPONED"}.get(status_code, "SCHEDULED")

    end_date = None
    end_local = (dates.get("end") or {}).get("localDate")
    if category[0] == "EXHIBITION" and end_local:
        try:
            if dt.date.fromisoformat(end_local) > day:
                end_date = end_local
        except ValueError:
            pass

    embedded = raw.get("_embedded") or {}
    venue_raw = (embedded.get("venues") or [{}])[0]
    venue = None
    if venue_raw.get("id") and venue_raw.get("name"):
        venue = {"id": f"tm:{venue_raw['id']}", "name": venue_raw["name"].strip(), "city": city_dto(city)}
        line = ((venue_raw.get("address") or {}).get("line1") or "").strip()
        if line:
            venue["address"] = line
        loc = venue_raw.get("location") or {}
        try:
            venue["coordinates"] = {"lat": float(loc["latitude"]), "lng": float(loc["longitude"])}
        except (KeyError, TypeError, ValueError):
            pass

    artist = None
    for attraction in embedded.get("attractions") or []:
        if attraction.get("id") and attraction.get("name"):
            genres = []
            acls = (attraction.get("classifications") or [{}])[0]
            for key in ("genre", "subGenre"):
                g = ((acls.get(key) or {}).get("name") or "").strip()
                if g and g.lower() != "undefined" and g not in genres:
                    genres.append(g)
            artist = {"id": f"tm:{attraction['id']}", "name": attraction["name"].strip(), "genres": genres}
            img = _image(attraction.get("images"))
            if img:
                artist["image_url"], artist["image_attribution"] = img, SOURCE
            break

    offer = {"provider": SOURCE, "url": url, "offer_type": "PRIMARY"}
    for price in raw.get("priceRanges") or []:
        try:
            low, high, cur = float(price["min"]), float(price["max"]), str(price["currency"])
            if 0 <= low <= high and re.fullmatch(r"[A-Z]{3}", cur):
                offer["price_range"] = {"min": low, "max": high, "currency": cur}
                break
        except (KeyError, TypeError, ValueError):
            continue

    event = {
        "id": f"tm:{eid}", "title": title, "category": {"id": category[0]},
        "city": city_dto(city),
        "schedule": {"start_datetime_utc": utc, "local_date": local_date,
                     "is_date_range": end_date is not None},
        "ticket_offers": [offer],
        "source_attribution": {"primary_source": SOURCE, "last_synced_at": synced_at},
        "status": status,
    }
    if category[1]:
        event["subcategory"] = category[1]
    if local_time:
        event["schedule"]["local_time"] = local_time
    if end_date:
        event["schedule"]["end_date"] = end_date
    if venue:
        event["venue"] = venue
    if artist:
        event["artist"] = artist
    image = _image(raw.get("images"))
    if image:
        event["media"] = {"image_url": image, "attribution": SOURCE}
    return event


def _geohash(lat: float, lng: float, precision: int = 7) -> str:
    base32, bits = "0123456789bcdefghjkmnpqrstuvwxyz", [16, 8, 4, 2, 1]
    lat_r, lng_r, out, bit, ch, even = [-90.0, 90.0], [-180.0, 180.0], [], 0, 0, True
    while len(out) < precision:
        rng, val = (lng_r, lng) if even else (lat_r, lat)
        mid = (rng[0] + rng[1]) / 2
        if val > mid:
            ch |= bits[bit]
            rng[0] = mid
        else:
            rng[1] = mid
        even = not even
        if bit < 4:
            bit += 1
        else:
            out.append(base32[ch])
            bit, ch = 0, 0
    return "".join(out)


def fetch_city(city: dict, key: str, now: dt.datetime) -> list[dict]:
    """Download raw Ticketmaster events for one city (all pages)."""
    results: list[dict] = []
    params = {
        "apikey": key, "geoPoint": _geohash(city["lat"], city["lng"]), "radius": str(city.get("radius_km", 15)),
        "unit": "km", "countryCode": city["country_code"], "segmentId": SEGMENTS,
        "startDateTime": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "endDateTime": (now + dt.timedelta(days=HORIZON_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sort": "date,asc", "size": str(PAGE_SIZE), "includeTBA": "no", "includeTBD": "no",
    }
    for page in range(MAX_PAGES):
        params["page"] = str(page)
        url = API + "?" + urllib.parse.urlencode(params)
        data = None
        for attempt in range(4):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "gsr-core-data/1.0", "Accept": "application/json"})
                with urllib.request.urlopen(req, timeout=30) as resp:
                    data = json.load(resp)
                break
            except urllib.error.HTTPError as err:
                # Never print the URL: it contains the API key.
                if err.code in (429, 500, 502, 503, 504) and attempt < 3:
                    time.sleep(2 * (attempt + 1))
                    continue
                print(f"  {city['id']}: HTTP {err.code} on page {page}", file=sys.stderr)
                if err.code in (401, 403):
                    raise SystemExit("Ticketmaster rejected the API key (HTTP %d)." % err.code)
                return results
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as err:
                if attempt < 3:
                    time.sleep(2 * (attempt + 1))
                    continue
                print(f"  {city['id']}: network error on page {page}: {type(err).__name__}", file=sys.stderr)
                return results
        if data is None:
            return results
        results.extend((data.get("_embedded") or {}).get("events") or [])
        info = data.get("page") or {}
        time.sleep(0.25)  # stay well under 5 requests/second
        if page + 1 >= int(info.get("totalPages") or 0) or (page + 2) * PAGE_SIZE > 1000:
            break
    return results


def _write(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def _sort_key(event: dict):
    return (event["schedule"]["start_datetime_utc"], event["id"])


def _interleave(groups: list[list[dict]], cap: int) -> list[dict]:
    out, index = [], 0
    while len(out) < cap and any(index < len(g) for g in groups):
        for g in groups:
            if index < len(g) and len(out) < cap:
                out.append(g[index])
        index += 1
    return sorted(out, key=_sort_key)


def build(config: dict, raw_by_city: dict[str, list[dict]], out: Path, synced_at: str,
          extra: dict[str, list[dict]] | None = None) -> dict:
    """raw_by_city holds raw Ticketmaster events; extra holds already normalized events (Biletinial)."""
    cities = {c["id"]: c for c in config["cities"]}
    by_city: dict[str, list[dict]] = {}
    for city_id in sorted(set(raw_by_city) | set(extra or {})):
        city = cities[city_id]
        seen, events = set(), []
        candidates = [normalize(raw, city, synced_at) for raw in raw_by_city.get(city_id, [])]
        candidates += (extra or {}).get(city_id, [])
        for event in candidates:
            if event and event["id"] not in seen and event["status"] == "SCHEDULED":
                seen.add(event["id"])
                events.append(event)
        events.sort(key=_sort_key)
        if events:
            by_city[city_id] = events[:CITY_CAP]

    for city_id, events in by_city.items():
        _write(out / "v1" / "events" / f"city-{city_id}.json", {"events": events, "has_more": False})

    countries: dict[str, list[dict]] = {}
    for city_id, events in by_city.items():
        countries.setdefault(cities[city_id]["country_code"], []).append(events)
    for code, groups in countries.items():
        _write(out / "v1" / "events" / f"country-{code}.json",
               {"events": _interleave(groups, COUNTRY_CAP), "has_more": False})
    _write(out / "v1" / "events" / "global.json",
           {"events": _interleave(list(by_city.values()), GLOBAL_CAP), "has_more": False})

    _write(out / "v1" / "locations.json", {
        "countries": [{"iso_code": code, "name": config["countries"][code]["name"],
                       "default_currency": config["countries"][code]["currency"]} for code in sorted(countries)],
        "cities": [city_dto(cities[cid]) for cid in sorted(by_city, key=lambda c: cities[c]["name"]["en"])],
    })

    artists: dict[str, dict] = {}
    tours: dict[str, list[dict]] = {}
    for events in by_city.values():
        for event in events:
            artist = event.get("artist")
            if artist:
                artists.setdefault(artist["id"], artist)
                tours.setdefault(artist["id"], []).append(event)
    ranked = sorted(artists, key=lambda a: (-len(tours[a]), artists[a]["name"].lower()))[:ARTIST_CAP]
    _write(out / "v1" / "artists.json", {"artists": [artists[a] for a in sorted(ranked, key=lambda a: artists[a]["name"].lower())]})
    for artist_id in ranked:
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", artist_id)
        _write(out / "v1" / "artists" / f"{safe}.json", {"events": sorted(tours[artist_id], key=_sort_key), "has_more": False})

    providers = {e["source_attribution"]["primary_source"] for events in (extra or {}).values() for e in events}
    sources = ([SOURCE] if raw_by_city else []) + sorted(providers)
    meta = {"generated_at": synced_at, "source": ", ".join(sources) or SOURCE,
            "cities": {cid: len(ev) for cid, ev in sorted(by_city.items())},
            "events_total": sum(len(ev) for ev in by_city.values()), "artists": len(ranked)}
    _write(out / "v1" / "meta.json", meta)
    partners = Path(__file__).with_name("partners.json")
    if partners.exists():
        hosts = json.loads(partners.read_text(encoding="utf-8")).get("hosts", {})
        _write(out / "v1" / "partners.json", {"hosts": hosts})
    (out / ".nojekyll").write_text("", encoding="utf-8")
    return meta


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default="site")
    parser.add_argument("--config", default=str(Path(__file__).with_name("cities.json")))
    parser.add_argument("--fixture", help="JSON file {city_id: [raw Ticketmaster events]} used instead of the API")
    parser.add_argument("--min-events", type=int, default=1, help="fail (keep old data) below this many events")
    parser.add_argument("--sources", default="ticketmaster,biletinial,londontheatre,todaytix,songkick,germany",
                        help="comma list: ticketmaster (needs the key), biletinial (Turkey), londontheatre (London), todaytix (US/Sydney stage), songkick (concerts), germany (Berlin/Munich/Hamburg stages)")
    parser.add_argument("--extra-fixture", help="JSON file {city_id: [normalized events]} used instead of Biletinial")
    args = parser.parse_args(argv)

    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    synced_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    sources = {s.strip() for s in args.sources.split(",") if s.strip()}
    raw_by_city: dict[str, list[dict]] = {}
    extra: dict[str, list[dict]] = {}
    if args.fixture:
        raw_by_city = json.loads(Path(args.fixture).read_text(encoding="utf-8"))
    elif "ticketmaster" in sources:
        key = os.environ.get("TICKETMASTER_API_KEY", "").strip()
        if not key:
            print("TICKETMASTER_API_KEY is not set; skipping Ticketmaster.", file=sys.stderr)
        else:
            # Ticketmaster does not sell in Turkey; Turkish cities come from Biletinial.
            for city in config["cities"]:
                if city.get("biletinial"):
                    continue
                raw_by_city[city["id"]] = fetch_city(city, key, now)
                print(f"  {city['id']}: {len(raw_by_city[city['id']])} raw events")
    if args.extra_fixture:
        extra = json.loads(Path(args.extra_fixture).read_text(encoding="utf-8"))
    elif "biletinial" in sources and not args.fixture:
        import biletinial
        extra = biletinial.collect(config["cities"], city_dto, synced_at, now.date())
    if not args.fixture and not args.extra_fixture:
        import importlib
        for name in ("londontheatre", "todaytix", "songkick", "germany"):
            if name not in sources:
                continue
            try:
                found = importlib.import_module(name).collect(config["cities"], city_dto, synced_at, now.date())
            except Exception as err:  # one broken site must not stop the others
                print(f"{name} failed: {err!r}", file=sys.stderr)
                continue
            for city_id, events in found.items():
                extra.setdefault(city_id, []).extend(events)
    if not raw_by_city and not extra and not args.fixture:
        print("No source produced data.", file=sys.stderr)
        return 2
    meta = build(config, raw_by_city, Path(args.out), synced_at, extra)
    print(json.dumps(meta, ensure_ascii=False, indent=1))
    if meta["events_total"] < args.min_events:
        print(f"Only {meta['events_total']} events; refusing to publish.", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
