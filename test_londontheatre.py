import datetime as dt
import json
import unittest

import biletinial
import londontheatre
from ingest import city_dto

CITY = {"id": "gb-london", "country_code": "GB", "timezone": "Europe/London", "lat": 51.5, "lng": -0.12,
        "name": {k: "London" for k in ("en", "tr", "de", "fr", "es", "it", "zh", "ar")}}


def page(items):
    return '<script type="application/ld+json">' + json.dumps(items) + "</script>"


SHOWS = [
    {"@type": "TheaterEvent", "name": "Hamilton", "url": "https://www.londontheatre.co.uk/show/20561-hamilton-tickets",
     "image": "https://images.ctfassets.net/x/hamilton.jpg", "startDate": "2021-08-19",
     "location": {"@type": "Place", "name": "Victoria Palace Theatre"},
     "offers": {"@type": "Offer", "price": 45, "priceCurrency": "GBP"}},
    {"@type": "TheaterEvent", "name": "Old Show", "url": "https://www.londontheatre.co.uk/show/1-old",
     "startDate": "2020-01-01", "endDate": "2020-02-01"},
    {"@type": "TheaterEvent", "name": "Elsewhere", "url": "https://evil.example/show", "startDate": "2026-12-01"},
    {"@type": "TheaterEvent", "name": "Short Run", "url": "https://www.londontheatre.co.uk/show/2-short",
     "startDate": "2026-12-01", "endDate": "2026-12-20"},
]


class LondonTheatreTest(unittest.TestCase):
    def test_running_shows_become_date_ranges_with_poster_and_ticket_link(self):
        today = dt.date(2026, 11, 19)
        events = {e["title"]: e for e in londontheatre.shows(page(SHOWS), "MUSICAL", CITY, city_dto, "2026-11-19T00:00:00Z", today)}
        self.assertEqual(set(events), {"Hamilton", "Short Run"})   # past and foreign links dropped
        ham = events["Hamilton"]
        self.assertEqual(ham["schedule"]["local_date"], "2026-11-19")
        self.assertEqual(ham["schedule"]["end_date"], "2027-02-17")
        self.assertEqual(ham["subcategory"], "MUSICAL")
        self.assertEqual(ham["media"]["image_url"], "https://images.ctfassets.net/x/hamilton.jpg")
        self.assertEqual(ham["ticket_offers"][0]["url"], "https://www.londontheatre.co.uk/show/20561-hamilton-tickets")
        self.assertEqual(ham["venue"]["name"], "Victoria Palace Theatre")
        self.assertEqual(events["Short Run"]["schedule"]["local_date"], "2026-12-01")
        self.assertEqual(events["Short Run"]["schedule"]["end_date"], "2026-12-20")

    def test_general_pages_guess_the_type_from_the_title(self):
        self.assertEqual(londontheatre.classify("The Nutcracker", None), "BALLET")
        self.assertEqual(londontheatre.classify("Tosca - Royal Opera", None), "OPERA")
        self.assertEqual(londontheatre.classify("Hamlet", None), "THEATRE")


class BiletinialTypesTest(unittest.TestCase):
    def test_exhibitions_are_kept_from_mixed_event_lists(self):
        self.assertEqual(biletinial.classify("etkinlik", "Frida Kahlo'nun Günlükleri Sergisi"), ("EXHIBITION", "ART"))
        self.assertEqual(biletinial.classify("etkinlik", "Astra Lumina İstanbul"), ("EXHIBITION", "TECHNOLOGY"))
        self.assertIsNone(biletinial.classify("etkinlik", "Kadıköy Quiz Night"))

    def test_concert_genres(self):
        self.assertEqual(biletinial.classify("muzik", "Candlelight: Vivaldi Dört Mevsim"), ("CONCERT", "CLASSICAL"))
        self.assertEqual(biletinial.classify("muzik", "90'lar Pop Gecesi"), ("CONCERT", "POP"))


if __name__ == "__main__":
    unittest.main()
