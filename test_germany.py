import datetime as dt
import json
import unittest

import germany
from ingest import city_dto

HAMBURG = {"id": "de-hamburg", "country_code": "DE", "timezone": "Europe/Berlin", "lat": 53.55, "lng": 10.0,
           "name": {k: "Hamburg" for k in ("en", "tr", "de", "fr", "es", "it", "zh", "ar")}}
TODAY = dt.date(2026, 11, 19)

ICS = """BEGIN:VCALENDAR\r
VERSION:2.0\r
BEGIN:VEVENT\r
UID:278340@die-hamburgische-staatsoper.de\r
DTSTART;TZID=Europe/Berlin:20261121T193000\r
LOCATION:Staatsoper\\, Großes Haus\r
SUMMARY:Wunderland\r
END:VEVENT\r
END:VCALENDAR\r
"""
TOUR_ICS = ICS.replace("SUMMARY:Wunderland", "SUMMARY:Führung durch die Staatsoper")
PAGE = ('<a href="/de/programm/ballett/1911-wunderland/278340.ics">ics</a>'
        '<a href="/de/programm/details/1-fuehrung/9.ics">ics</a>'
        '<a href="https://evil.example/x.ics">ics</a>')
PLAY = '<meta property="og:image" content="https://www.die-hamburgische-staatsoper.de/img/wunderland.jpg">'


class GermanyIcsTest(unittest.TestCase):
    def test_performances_from_calendar_files_with_poster_and_play_page(self):
        pages = {
            "https://www.die-hamburgische-staatsoper.de/de/programm/ballett/1911-wunderland/278340.ics": ICS,
            "https://www.die-hamburgische-staatsoper.de/de/programm/details/1-fuehrung/9.ics": TOUR_ICS,
            "https://www.die-hamburgische-staatsoper.de/de/programm/ballett/1911-wunderland": PLAY,
        }
        events = germany.ics_events(PAGE, HAMBURG, city_dto, "s", TODAY, "Hamburg Ballett", "BALLET",
                                    "https://www.die-hamburgische-staatsoper.de/de/kalender/ballett", fetch=pages.get)
        self.assertEqual(len(events), 1)          # the guided tour and the foreign link are left out
        e = events[0]
        self.assertEqual(e["title"], "Wunderland")
        self.assertEqual(e["subcategory"], "BALLET")
        self.assertEqual(e["schedule"]["local_date"], "2026-11-21")
        self.assertEqual(e["schedule"]["local_time"], "19:30")
        self.assertEqual(e["schedule"]["start_datetime_utc"], "2026-11-21T18:30:00Z")
        self.assertEqual(e["venue"]["name"], "Staatsoper, Großes Haus")
        self.assertEqual(e["ticket_offers"][0]["url"], "https://www.die-hamburgische-staatsoper.de/de/programm/ballett/1911-wunderland")
        self.assertEqual(e["media"]["image_url"], "https://www.die-hamburgische-staatsoper.de/img/wunderland.jpg")

    def test_whole_words_only_when_skipping(self):
        self.assertTrue(germany.skipped("Führung Spezial Bühnenbild"))
        self.assertFalse(germany.skipped("Uraufführung: Faust"))
        self.assertTrue(germany.skipped("Ballett-Werkstatt"))

    def test_description_link_wins_for_residenztheater(self):
        vevent = {"description": "Alle Infos zum Stück: https://www.residenztheater.de/stuecke/detail/hamlet"}
        self.assertEqual(germany.production_link("https://www.residenztheater.de/calendarics.ics?x=1", vevent,
                                                 "https://www.residenztheater.de/spielplan"),
                         "https://www.residenztheater.de/stuecke/detail/hamlet")


class GermanyJsonLdTest(unittest.TestCase):
    def test_programme_page_events(self):
        items = [{"@type": "TheaterEvent", "name": "Een Mann för all Fäll", "startDate": "2026-11-20T19:30:00+01:00",
                  "url": "https://www.ohnsorg.de/events/een-mann/", "image": "https://www.ohnsorg.de/p.jpg",
                  "location": {"@type": "PerformingArtsTheater", "name": "Ohnsorg-Theater, Großes Haus"}},
                 {"@type": "TheaterEvent", "name": "Old", "startDate": "2026-01-01T19:30:00+01:00",
                  "url": "https://www.ohnsorg.de/events/old/"},
                 {"@type": "TheaterEvent", "name": "Elsewhere", "startDate": "2026-12-01T19:30:00+01:00",
                  "url": "https://evil.example/x"}]
        page = '<script type="application/ld+json">' + json.dumps(items) + "</script>"
        events = germany.jsonld_events(page, HAMBURG, city_dto, "s", TODAY, "Ohnsorg-Theater", "THEATRE",
                                       "https://www.ohnsorg.de/spielplan/")
        self.assertEqual([e["title"] for e in events], ["Een Mann för all Fäll"])
        self.assertEqual(events[0]["schedule"]["local_time"], "19:30")
        self.assertEqual(events[0]["media"]["image_url"], "https://www.ohnsorg.de/p.jpg")


if __name__ == "__main__":
    unittest.main()
