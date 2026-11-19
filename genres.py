"""Genre for concerts whose source gives none (most Biletinial concerts).

Only classifies real listed concerts, from their title: clear keywords first ("tribute to Pink
Floyd", "Senfoni", "Jazz"), then the well-known genre of artists who often tour Türkiye. A concert
that matches nothing stays without a type and is still shown under "All".
"""
from __future__ import annotations

import re
import unicodedata


def fold(text: str) -> str:
    text = (text or "").replace("İ", "i").replace("I", "ı").lower().replace("ı", "i")
    text = "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))
    return " " + re.sub(r"[^a-z0-9]+", " ", text).strip() + " "


# Bands and singers whose genre is well established (folded, as whole words).
ARTISTS = {
    "ROCK": [
        "duman", "mor ve otesi", "manga", "teoman", "sebnem ferah", "athena", "gripin", "kurban", "badem",
        "demir demirkan", "haluk levent", "cem karaca", "baris manco", "erkin koray", "altin gun", "yavuz cetin",
        "kurtalan ekspres", "adamlar", "dolu kadehi ters tut", "buyuk ev ablukada", "hayko cepkin",
        "pentagram", "mezarkabul", "redd", "kargo", "bulutsuz", "feridun duzagac", "yuksek sadakat", "seksendort",
        "pinhani", "kolpa", "kalben", "ozlem tekin", "aylin aslim", "kurtalan", "mfo", "the ringo jets",
        "gaye su akyol", "baba zula", "replikas", "hey douglas", "cem adrian", "son feci bisiklet", "palmiyeler",
        "triggerfinger", "pink floyd", "floydian", "queen", "beatles", "metallica", "nirvana", "arctic monkeys",
        "led zeppelin", "rolling stones", "ac dc", "guns n roses", "bon jovi", "linkin park", "red hot chili peppers",
        "u2", "coldplay", "radiohead", "muse", "foo fighters", "the cure", "depeche mode", "deep purple",
        "iron maiden", "scorpions", "dire straits", "eric clapton", "jimi hendrix", "doors", "bulent ortacgil",
        "zakkum", "jakuzi", "can ozan", "emre aydin", "halil sezai", "ogun sanlisoy", "kirac", "rock sympho",
    ],
    "POP": [
        "tarkan", "sezen aksu", "sertab erener", "hande yener", "kenan dogulu", "gulsen", "edis", "hadise",
        "murat boz", "mustafa sandal", "soner sarikabadayi", "serdar ortac", "sila", "goksel", "simge",
        "zeynep bastik", "aleyna tilki", "melike sahin", "mabel matiz", "gokhan turkmen", "bengu", "demet akalin",
        "yalin", "ozan dogulu", "ajda pekkan", "nil karaibrahimgil", "manus baba", "buray", "oguzhan koc",
        "ebru gundes", "funda arar", "ferhat gocer", "levent yuksel", "ilyas yalcintas", "candan ercetin",
        "sezen", "ismail yk", "berkay", "irem derici", "derya ulug", "ece seckin", "merve ozbey",
        "mert demir", "ufuk beydemir", "zeynep casalini", "ajda", "yildiz tilbe", "sinan akcil",
        "abba", "michael jackson", "madonna", "whitney houston", "elvis", "britney", "taylor swift", "adele",
        "90 lar", "90lar", "80 ler", "80ler", "2000 ler", "2000ler", "pop gecesi", "nihat sirdar", "karsu",
        "batu akdeniz", "tuana", "can bonomo", "ezhel", "sefo", "uzi", "lvbel c5", 
        "ben fero", "sagopa kajmer", "ceza", "norm ender", "gazapizm", "hidra", "rap",
    ],
    "ELECTRONIC": [
        "burak yeter", "mahmut orhan", "ilkay sencan", "boral kibil", "mercan dede", "dj ", "techno", "afterparty", "after party", "rave", "elektronik", "electronic", "edm",
    ],
    "JAZZ": [
        "kerem gorsev", "ilhan ersahin", "jazz", "caz", "blues", "swing", "big band", "bossa", "soul", "funk",
        "karsu jazz", "fatih erkoc",
    ],
    "CLASSICAL": [
        "senfoni", "orkestra", "orchestra", "filarmoni", "philharmon", "oda muzigi", "resital", "recital",
        "piyano", "piano", "keman", "violin", "viyolonsel", "cello", "koro", "choir", "mozart", "beethoven",
        "bach", "vivaldi", "chopin", "tchaikovsky", "cayikovski", "candlelight", "evgeny grinko", "fazil say",
        "idil biret", "gulsin onay", "klasik", "classical", "barok", "baroque", "requiem", "opera gala",
    ],
    "FESTIVAL": ["festival", "fest "],
}
# More specific types first: a "Jazz Orchestra" is jazz, a "Rock Sympho Show" is rock.
ORDER = ["FESTIVAL", "ELECTRONIC", "JAZZ", "ROCK", "POP", "CLASSICAL"]
_PATTERNS = {kind: re.compile(r" (" + "|".join(re.escape(fold(w).strip()) for w in ARTISTS[kind]) + r") ")
             for kind in ORDER}


def concert_genre(title: str) -> str | None:
    text = fold(title)
    for kind in ORDER:
        if _PATTERNS[kind].search(text):
            return kind
    return None


def fill(events: list[dict]) -> int:
    """Give a type to untyped concerts in place; returns how many got one."""
    filled = 0
    for event in events:
        if event.get("category", {}).get("id") == "CONCERT" and not event.get("subcategory"):
            kind = concert_genre(event.get("title", ""))
            if kind:
                event["subcategory"] = kind
                filled += 1
    return filled
