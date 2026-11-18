"""Offline tests for ingest.py (no network, no API key)."""
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

import ingest

CONFIG = json.loads(Path(__file__).with_name("cities.json").read_text(encoding="utf-8"))
LONDON = next(c for c in CONFIG["cities"] if c["id"] == "gb-london")
FUTURE = (dt.date.today() + dt.timedelta(days=10)).isoformat()
LATER = (dt.date.today() + dt.timedelta(days=40)).isoformat()


def raw(eid, name, segment, genre, sub="Undefined", **extra):
    event = {
        "id": eid, "name": name, "url": f"https://www.ticketmaster.co.uk/event/{eid}",
        "classifications": [{"segment": {"name": segment}, "genre": {"name": genre}, "subGenre": {"name": sub}}],
        "dates": {"start": {"localDate": FUTURE, "localTime": "19:30:00", "dateTime": f"{FUTURE}T19:30:00Z"},
                  "status": {"code": "onsale"}},
        "images": [{"ratio": "16_9", "width": 1024, "url": "https://s1.ticketm.net/img/a.jpg"},
                   {"ratio": "3_2", "width": 305, "url": "https://s1.ticketm.net/img/b.jpg"}],
        "priceRanges": [{"min": 25.0, "max": 80.0, "currency": "GBP"}],
        "_embedded": {
            "venues": [{"id": "V1", "name": "Royal Albert Hall", "address": {"line1": "Kensington Gore"},
                        "location": {"latitude": "51.5009", "longitude": "-0.1774"}}],
            "attractions": [{"id": "A1", "name": "Test Orchestra",
                             "classifications": [{"genre": {"name": "Classical"}, "subGenre": {"name": "Orchestral"}}]}],
        },
    }
    event.update(extra)
    return event


class ClassifyTest(unittest.TestCase):
    def check(self, expected, *args, **kw):
        self.assertEqual(expected, ingest.classify(raw("x", *args, **kw)))

    def test_music(self):
        self.check(("CONCERT", "ROCK"), "Band live", "Music", "Rock")
        self.check(("CONCERT", "CLASSICAL"), "Symphony", "Music", "Classical")
        self.check(("CONCERT", "ELECTRONIC"), "DJ night", "Music", "Dance/Electronic")
        self.check(("CONCERT", "FESTIVAL"), "Summer Festival 2027", "Music", "Pop")
        self.check(("CONCERT", None), "Unknown genre", "Music", "Other")

    def test_stage(self):
        self.check(("THEATRE_MUSICAL", "MUSICAL"), "Les Mis", "Arts & Theatre", "Theatre", "Musical")
        self.check(("THEATRE_MUSICAL", "OPERA"), "La Traviata", "Arts & Theatre", "Opera")
        self.check(("THEATRE_MUSICAL", "BALLET"), "The Nutcracker", "Arts & Theatre", "Dance", "Ballet")
        self.check(("THEATRE_MUSICAL", "THEATRE"), "Hamlet", "Arts & Theatre", "Theatre", "Drama")

    def test_exhibition_and_skips(self):
        self.check(("EXHIBITION", "ART"), "Van Gogh Exhibition", "Miscellaneous", "Undefined")
        self.assertIsNone(ingest.classify(raw("x", "Stand-up night", "Arts & Theatre", "Comedy")))
        self.assertIsNone(ingest.classify(raw("x", "Football", "Sports", "Soccer")))


class NormalizeTest(unittest.TestCase):
    def test_full_event_maps_to_app_contract(self):
        event = ingest.normalize(raw("E1", "Mahler 5", "Music", "Classical"), LONDON, "2026-11-19T00:00:00Z")
        self.assertEqual("tm:E1", event["id"])
        self.assertEqual({"id": "CONCERT"}, event["category"])
        self.assertEqual("CLASSICAL", event["subcategory"])
        self.assertEqual("19:30", event["schedule"]["local_time"])
        self.assertEqual("gb-london", event["city"]["id"])
        self.assertEqual("Royal Albert Hall", event["venue"]["name"])
        self.assertEqual({"lat": 51.5009, "lng": -0.1774}, event["venue"]["coordinates"])
        self.assertEqual("https://s1.ticketm.net/img/a.jpg", event["media"]["image_url"])
        offer = event["ticket_offers"][0]
        self.assertEqual(("Ticketmaster", "PRIMARY"), (offer["provider"], offer["offer_type"]))
        self.assertTrue(offer["url"].startswith("https://"))
        self.assertEqual({"min": 25.0, "max": 80.0, "currency": "GBP"}, offer["price_range"])
        self.assertEqual("Test Orchestra", event["artist"]["name"])
        self.assertEqual("Ticketmaster", event["source_attribution"]["primary_source"])

    def test_missing_utc_is_computed_from_city_timezone(self):
        r = raw("E2", "Hamlet", "Arts & Theatre", "Theatre")
        r["dates"]["start"] = {"localDate": "2027-07-01", "localTime": "19:30:00"}
        event = ingest.normalize(r, LONDON, "2026-11-19T00:00:00Z")
        self.assertEqual("2027-07-01T18:30:00Z", event["schedule"]["start_datetime_utc"])  # BST = UTC+1

    def test_unsafe_or_incomplete_events_are_dropped(self):
        self.assertIsNone(ingest.normalize(raw("E3", "x", "Music", "Rock", url="http://insecure"), LONDON, "t"))
        no_date = raw("E4", "x", "Music", "Rock")
        no_date["dates"]["start"] = {}
        self.assertIsNone(ingest.normalize(no_date, LONDON, "t"))

    def test_cancelled_status(self):
        r = raw("E5", "x", "Music", "Rock")
        r["dates"]["status"]["code"] = "cancelled"
        self.assertEqual("CANCELLED", ingest.normalize(r, LONDON, "t")["status"])

    def test_geohash(self):
        self.assertEqual("gcpvj0d", ingest._geohash(51.5074, -0.1278))


class BuildTest(unittest.TestCase):
    def test_build_writes_all_files_and_skips_empty_cities(self):
        rock = raw("R1", "Band", "Music", "Rock")
        dup = raw("R1", "Band", "Music", "Rock")
        cancelled = raw("C1", "Off", "Music", "Rock")
        cancelled["dates"]["status"]["code"] = "cancelled"
        late = raw("L1", "Opera night", "Arts & Theatre", "Opera")
        late["dates"]["start"] = {"localDate": LATER, "localTime": "20:00:00", "dateTime": f"{LATER}T20:00:00Z"}
        fixture = {"gb-london": [late, rock, dup, cancelled], "de-berlin": [raw("B1", "Techno", "Music", "Dance/Electronic")],
                   "tr-istanbul": []}
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            meta = ingest.build(CONFIG, fixture, out, "2026-11-19T00:00:00Z")
            self.assertEqual({"de-berlin": 1, "gb-london": 2}, meta["cities"])
            load = lambda p: json.loads((out / "v1" / p).read_text(encoding="utf-8"))
            london = load("events/city-gb-london.json")["events"]
            self.assertEqual(["tm:R1", "tm:L1"], [e["id"] for e in london])  # sorted, deduped, cancelled dropped
            locations = load("locations.json")
            self.assertEqual(["Berlin", "London"], [c["name"]["en"] for c in locations["cities"]])
            self.assertEqual({"DE", "GB"}, {c["iso_code"] for c in locations["countries"]})
            self.assertEqual("伦敦", locations["cities"][1]["name"]["zh"])
            self.assertEqual(3, len(load("events/global.json")["events"]))
            self.assertEqual(2, len(load("events/country-GB.json")["events"]))
            self.assertFalse((out / "v1" / "events" / "city-tr-istanbul.json").exists())
            self.assertEqual(["Test Orchestra"], [a["name"] for a in load("artists.json")["artists"]])
            self.assertEqual(3, len(load("artists/tm_A1.json")["events"]))

    def test_cli_refuses_to_publish_empty_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Path(tmp) / "f.json"
            fixture.write_text("{}", encoding="utf-8")
            self.assertEqual(3, ingest.main(["--out", str(Path(tmp) / "site"), "--fixture", str(fixture)]))


if __name__ == "__main__":
    unittest.main()
