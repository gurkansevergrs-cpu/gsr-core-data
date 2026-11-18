# gsr-core-data

Live event data for the **GSR CORE — Core of Arts** Android app.

Twice a day a GitHub Actions job (Linux runner, free on a public repository)
downloads upcoming concerts, stage shows and exhibitions from the free
Ticketmaster Discovery API, converts them to the app's format and publishes
static JSON files to the `data` branch. The app reads them from:

    https://raw.githubusercontent.com/<owner>/gsr-core-data/data/

GSR CORE does not sell tickets. Every event links to its official Ticketmaster
page.

## One-time setup (owner)

1. Get a free API key at https://developer.ticketmaster.com (My Apps → Consumer Key).
2. Create a **public** GitHub repository named `gsr-core-data` and push this folder.
3. Repository → Settings → Secrets and variables → Actions → New repository secret:
   name `TICKETMASTER_API_KEY`, value = the Consumer Key.
4. Actions tab → "Refresh GSR CORE events" → Run workflow.

The job refuses to publish if fewer than 50 events come back, so a bad key or
an API outage never wipes the existing data.

## Local test (no key needed)

    python -m unittest -v test_ingest

## Cities

Edit `cities.json` to add or remove cities. Cities without events are left out
of `v1/locations.json` automatically.
