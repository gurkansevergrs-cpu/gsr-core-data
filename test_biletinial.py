"""Offline tests for biletinial.py (no network)."""
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

import biletinial
import ingest

CONFIG = json.loads(Path(__file__).with_name("cities.json").read_text(encoding="utf-8"))
TR = [c for c in CONFIG["cities"] if c.get("biletinial")]
BY_NAME = {biletinial.fold(c["name"]["tr"]): c for c in TR}
TODAY = dt.date(2026, 11, 19)


def page(*events, item_list=None):
    blocks = []
    if item_list:
        blocks.append({"@context": "https://schema.org", "@type": "ItemList",
                       "itemListElement": [{"@type": "ListItem", "position": i + 1, "url": u} for i, u in enumerate(item_list)]})
    if events:
        blocks.append(list(events))
    return "<html><head>" + "".join(
        f'<script type="application/ld+json">{json.dumps(b, ensure_ascii=False)}</script>' for b in blocks) + "</head></html>"


def perf(name="TAVŞAN\xa0TAVŞANOĞLU", start="2026-12-01T20:00:00+03:00", region="Ankara", hall="Mavi Salon", price=120.0):
    return {"@type": "Event", "name": name, "image": "https://cdn.example/Uploads/a b.jpg",
            "url": "https://biletinial.com/tr-tr/tiyatro/tavsan-dt", "startDate": start,
            "eventStatus": "https://schema.org/EventScheduled",
            "location": {"@type": "Place", "name": hall,
                         "address": {"streetAddress": "Cad. 13", "addressLocality": region, "addressRegion": region},
                         "geo": {"latitude": "39.93", "longitude": "32.85"}},
            "offers": {"price": price, "priceCurrency": "TRY"}}


class BiletinialTest(unittest.TestCase):
    def test_all_81_provinces_are_configured_once(self):
        self.assertEqual(81, len(TR))
        self.assertEqual(81, len({c["biletinial"] for c in TR}))
        self.assertTrue(all(c["country_code"] == "TR" and c["timezone"] == "Europe/Istanbul" for c in TR))
        for name in ("İstanbul", "Şanlıurfa", "Iğdır", "Elazığ", "Çorum", "Eskişehir"):
            self.assertIn(biletinial.fold(name), BY_NAME, name)

    def test_fold_matches_turkish_spellings(self):
        self.assertEqual("istanbul", biletinial.fold("İSTANBUL"))
        self.assertEqual("igdir", biletinial.fold("IĞDIR"))
        self.assertEqual("elazig", biletinial.fold("Elâzığ"))

    def test_classify(self):
        self.assertEqual(("THEATRE_MUSICAL", "THEATRE"), biletinial.classify("tiyatro", "Hamlet"))
        self.assertEqual(("THEATRE_MUSICAL", "MUSICAL"), biletinial.classify("tiyatro", "Öteki Ankara Müzikali"))
        self.assertIsNone(biletinial.classify("tiyatro", "Ali Congun Stand Up"))
        self.assertEqual(("THEATRE_MUSICAL", "BALLET"), biletinial.classify("opera-bale", "Kuğu Gölü Balesi"))
        self.assertEqual(("THEATRE_MUSICAL", "OPERA"), biletinial.classify("opera-bale", "Tosca"))
        self.assertEqual(("CONCERT", "CLASSICAL"), biletinial.classify("muzik", "Cumhurbaşkanlığı Senfoni Orkestrası"))
        self.assertEqual(("CONCERT", "JAZZ"), biletinial.classify("muzik", "Caz Gecesi"))

    def test_list_urls(self):
        urls = ["https://biletinial.com/tr-tr/tiyatro/a", "https://biletinial.com/tr-tr/tiyatro/b", "https://evil.example/x"]
        self.assertEqual(urls[:2], biletinial.list_urls(page(item_list=urls)))

    def test_performances_map_to_their_province_with_poster_and_ticket_link(self):
        html = page(perf(), perf(start="2026-12-05T15:00:00+03:00", region="İzmir", hall="Konak Sahnesi"),
                    perf(start="2026-10-01T20:00:00+03:00"),            # past: dropped
                    perf(region="Atlantis"))                              # unknown province: dropped
        out = biletinial.performances(html, "tiyatro", BY_NAME, ingest.city_dto, "2026-11-19T00:00:00Z", TODAY)
        self.assertEqual(["tr-ankara", "tr-izmir"], [c for c, _ in out])
        both_sides = page(perf(region="İstanbul Anadolu"), perf(start="2026-12-02T20:00:00+03:00", region="İstanbul Avrupa"))
        self.assertEqual(["tr-istanbul", "tr-istanbul"],
                         [c for c, _ in biletinial.performances(both_sides, "tiyatro", BY_NAME, ingest.city_dto, "x", TODAY)])
        event = out[0][1]
        self.assertEqual("TAVŞAN TAVŞANOĞLU", event["title"])
        self.assertEqual("2026-12-01T17:00:00Z", event["schedule"]["start_datetime_utc"])
        self.assertEqual(("2026-12-01", "20:00"), (event["schedule"]["local_date"], event["schedule"]["local_time"]))
        self.assertEqual("https://biletinial.com/tr-tr/tiyatro/tavsan-dt", event["ticket_offers"][0]["url"])
        self.assertEqual("Biletinial", event["ticket_offers"][0]["provider"])
        self.assertEqual("https://cdn.example/Uploads/a%20b.jpg", event["media"]["image_url"])
        self.assertEqual("Mavi Salon", event["venue"]["name"])
        self.assertEqual("THEATRE", event["subcategory"])

    def test_build_writes_turkish_city_files(self):
        html = page(perf(start="2027-01-10T20:00:00+03:00"))
        today = dt.date(2027, 1, 1)
        events = {}
        for city_id, event in biletinial.performances(html, "tiyatro", BY_NAME, ingest.city_dto, "x", today):
            events.setdefault(city_id, []).append(event)
        with tempfile.TemporaryDirectory() as tmp:
            meta = ingest.build(CONFIG, {}, Path(tmp), "2027-01-01T00:00:00Z", events)
            self.assertEqual({"tr-ankara": 1}, meta["cities"])
            city = json.loads((Path(tmp) / "v1/events/city-tr-ankara.json").read_text(encoding="utf-8"))
            self.assertEqual("TAVŞAN TAVŞANOĞLU", city["events"][0]["title"])
            self.assertTrue((Path(tmp) / "v1/events/country-TR.json").exists())



class BalletTest(unittest.TestCase):
    def test_ballets_without_the_word_are_found_and_operas_stay_operas(self):
        self.assertEqual(biletinial.classify("opera-bale", "Kuğu Gölü"), ("THEATRE_MUSICAL", "BALLET"))
        self.assertEqual(biletinial.classify("opera-bale", "Fındıkkıran"), ("THEATRE_MUSICAL", "BALLET"))
        self.assertEqual(biletinial.classify("opera-bale", "Tosca", "İzmir Devlet Opera ve Balesi sunar. Puccini'nin operası."),
                         ("THEATRE_MUSICAL", "OPERA"))


class NetworkErrorTest(unittest.TestCase):
    def test_a_dropped_secure_connection_skips_the_page_instead_of_crashing(self):
        import ssl
        from unittest import mock
        with mock.patch.object(biletinial, "allowed", return_value=True), \
                mock.patch.object(biletinial.time, "sleep"), \
                mock.patch.object(biletinial.urllib.request, "urlopen", side_effect=ssl.SSLError("record layer failure")):
            self.assertIsNone(biletinial.get("https://biletinial.com/tr-tr/tiyatro/x"))


class StandUpTest(unittest.TestCase):
    def test_stand_up_listed_under_theatre_is_left_out(self):
        self.assertIsNone(biletinial.classify("tiyatro", "Özlem Kosif - Hayatım Yanlış Anlama",
                                              "Özlem Kosif - Hayatım Yanlış Anlama Stand Up (Tek Kişilik Gösteri)"))
        page = '<span>Etkinlik Türü</span> <b>Stand up</b>'
        self.assertEqual(biletinial.page_kind(page), "stand up")
        self.assertEqual(biletinial.performances(page, "tiyatro", {}, lambda c: c, "s", __import__("datetime").date(2026, 11, 20)), [])

    def test_a_real_play_mentioning_cinema_actors_is_kept(self):
        self.assertEqual(biletinial.classify("tiyatro", "Hamlet", "Sinema ve tiyatro oyuncusu X'in yorumuyla."),
                         ("THEATRE_MUSICAL", "THEATRE"))

if __name__ == "__main__":
    unittest.main()
