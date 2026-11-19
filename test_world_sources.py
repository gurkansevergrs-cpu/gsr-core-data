import datetime as dt
import json
import unittest

import songkick
import todaytix
import londontheatre
from ingest import city_dto

BERLIN = {"id": "de-berlin", "country_code": "DE", "timezone": "Europe/Berlin", "lat": 52.52, "lng": 13.4,
          "name": {k: "Berlin" for k in ("en", "tr", "de", "fr", "es", "it", "zh", "ar")}}
NYC = dict(BERLIN, id="us-new-york", country_code="US", timezone="America/New_York")
TODAY = dt.date(2026, 11, 19)


def page(items):
    return '<script type="application/ld+json">' + json.dumps(items) + "</script>"


CONCERTS = [
    {"@type": "MusicEvent", "name": "Portugal. The Man @ Columbiahalle",
     "url": "https://www.songkick.com/concerts/42801639-portugal-the-man-at-columbiahalle?utm_medium=organic",
     "image": "https://images.sk-static.com/images/media/profile_images/artists/400418/huge_avatar",
     "location": {"name": "Columbiahalle", "address": {"streetAddress": "Columbiadamm 13-21"},
                  "geo": {"latitude": 52.48, "longitude": 13.39}},
     "startDate": "2026-11-24T20:00:00", "endDate": "2026-11-24",
     "performer": [{"@type": "MusicGroup", "name": "Portugal. The Man", "genre": ["indie_alternative", "rock"]}]},
    {"@type": "MusicEvent", "name": "Diyar Dersim 2026 @ UdK",
     "url": "https://www.songkick.com/festivals/3758235-diyar-dersim/id/43412666-diyar-dersim-2026",
     "location": {"name": "UdK"}, "startDate": "2026-10-10", "endDate": "2026-11-20", "performer": []},
    {"@type": "MusicEvent", "name": "Old Gig @ Somewhere", "url": "https://www.songkick.com/concerts/1-old",
     "startDate": "2026-01-01", "endDate": "2026-01-01"},
    {"@type": "MusicEvent", "name": "Phish @ Elsewhere", "url": "https://evil.example/concerts/2", "startDate": "2026-12-01"},
]


class SongkickTest(unittest.TestCase):
    def test_concerts_get_artist_name_genre_time_venue_and_image(self):
        events = {e["id"]: e for e in songkick.concerts(page(CONCERTS), BERLIN, city_dto, "2026-11-19T00:00:00Z", TODAY)}
        self.assertEqual(set(events), {"sk:42801639", "sk:43412666"})
        gig = events["sk:42801639"]
        self.assertEqual(gig["title"], "Portugal. The Man")
        self.assertEqual(gig["subcategory"], "ROCK")
        self.assertEqual(gig["schedule"]["local_date"], "2026-11-24")
        self.assertEqual(gig["schedule"]["local_time"], "20:00")
        self.assertEqual(gig["ticket_offers"][0]["url"], "https://www.songkick.com/concerts/42801639-portugal-the-man-at-columbiahalle")
        self.assertEqual(gig["venue"]["name"], "Columbiahalle")
        self.assertIn("media", gig)
        fest = events["sk:43412666"]
        self.assertEqual(fest["subcategory"], "FESTIVAL")
        self.assertEqual(fest["schedule"]["local_date"], "2026-11-19")   # running festival starts today
        self.assertEqual(fest["schedule"]["end_date"], "2026-11-20")

    def test_every_city_outside_turkey_has_a_metro(self):
        cities = json.load(open("cities.json", encoding="utf-8"))["cities"]
        abroad = {c["id"] for c in cities if not c.get("biletinial")}
        self.assertEqual(abroad, set(songkick.METROS))


class TodayTixTest(unittest.TestCase):
    def test_shows_use_todaytix_links_and_art_fairs_are_exhibitions(self):
        items = [{"@type": "TheaterEvent", "name": "Hamilton", "url": "https://www.todaytix.com/nyc/shows/1-hamilton",
                  "image": "https://images.ctfassets.net/x.png", "startDate": "2020-01-01"},
                 {"@type": "TheaterEvent", "name": "Frieze Los Angeles", "url": "https://www.todaytix.com/nyc/shows/2-frieze",
                  "startDate": "2026-12-01", "endDate": "2026-12-03"},
                 {"@type": "TheaterEvent", "name": "Comedy Cellar", "url": "https://www.todaytix.com/nyc/shows/3-comedy",
                  "startDate": "2026-12-01"}]
        events = {e["title"]: e for e in londontheatre.shows(page(items), "MUSICAL", NYC, city_dto, "s", TODAY,
                                                              base=todaytix.BASE, source=todaytix.SOURCE, prefix="tt")}
        self.assertEqual(set(events), {"Hamilton", "Frieze Los Angeles"})
        self.assertEqual(events["Hamilton"]["id"], "tt:1-hamilton")
        self.assertEqual(events["Hamilton"]["ticket_offers"][0]["provider"], "TodayTix")
        self.assertEqual(events["Frieze Los Angeles"]["category"]["id"], "EXHIBITION")


if __name__ == "__main__":
    unittest.main()
