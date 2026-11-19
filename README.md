# gsr-core-data

Live event data for the **GSR CORE — Core of Arts** Android app.

Once a day a GitHub Actions job (Linux runner, free on a public repository)
collects upcoming concerts, stage shows and exhibitions, converts them to the
app's format and publishes static JSON files to the `data` branch:

- **Turkey (all 81 provinces):** public Biletinial city pages
  (`/tr-tr/tiyatro|opera-bale|muzik/<city>`). Each play page carries schema.org
  Event data with the play name, poster, date, hall and ticket link. Biletinial
  also sells the State Theatres' tickets. robots.txt is honoured and requests
  are spaced one second apart (`biletinial.py`).
- **Other countries:** the free Ticketmaster Discovery API (Ticketmaster does
  not sell in Turkey).

The app reads the files from:

    https://raw.githubusercontent.com/<owner>/gsr-core-data/data/

GSR CORE does not sell tickets. Every event links to its ticket page on the
seller's own website (Biletinial or Ticketmaster).

## One-time setup (owner)

Turkey works without a key. For other countries:

1. Get a free API key at https://developer.ticketmaster.com (My Apps → Consumer Key).
2. Create a **public** GitHub repository named `gsr-core-data` and push this folder.
3. Repository → Settings → Secrets and variables → Actions → New repository secret:
   name `TICKETMASTER_API_KEY`, value = the Consumer Key.
4. Actions tab → "Refresh GSR CORE events" → Run workflow.

The job refuses to publish if fewer than 50 events come back, so a bad key or
an API outage never wipes the existing data.

## Local test (no key needed)

    python -m unittest -v test_ingest test_biletinial

Turkey only, without a Ticketmaster key (takes about 30-40 minutes):

    python ingest.py --out site --sources biletinial

## Cities

Edit `cities.json` to add or remove cities. Cities without events are left out
of `v1/locations.json` automatically.

## London

`londontheatre.py` reads the public What's On pages of londontheatre.co.uk (schema.org TheaterEvent data, robots.txt respected) so London works without a Ticketmaster key. Long-running shows are published as a date range over the next 90 days.

## Other cities (no API key needed)

- `todaytix.py`: stage shows in New York, Los Angeles, Chicago and Sydney from TodayTix city category pages (same page data as London Theatre).
- `songkick.py`: concerts and festivals in all 27 non-Turkish cities from Songkick metro-area pages (up to 4 pages of 50 per city).
- `webfetch.py`: shared polite fetching (robots.txt per host, 1 s spacing, retries). A source that fails is skipped; the others still publish.

## Partner codes (commission)

`partners.json` lists referral/affiliate parameters per ticket host. It is published as `v1/partners.json` and the app adds those parameters to ticket links for that host on its next start, never overriding a parameter the link already has. Put a commission code here once a partner programme is approved; no app update is needed.
