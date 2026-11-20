"""Polite page fetching shared by the listing sources: robots.txt per host, spaced requests, retries."""
from __future__ import annotations

import http.client
import json
import re
import socket
import sys
import time
import urllib.error
import urllib.request
import urllib.robotparser
from urllib.parse import urlsplit

USER_AGENT = "gsr-core-data/1.0 (event listing; links back to the ticket page)"
DELAY = 1.0
_robots: dict[str, urllib.robotparser.RobotFileParser] = {}


def allowed(url: str) -> bool:
    host = "{0.scheme}://{0.netloc}".format(urlsplit(url))
    if host not in _robots:
        parser = urllib.robotparser.RobotFileParser(host + "/robots.txt")
        old = socket.getdefaulttimeout()
        socket.setdefaulttimeout(30)
        try:
            parser.read()
        except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException):  # incl. ssl errors, resets
            parser = urllib.robotparser.RobotFileParser()
            parser.parse(["User-agent: *", "Disallow: /"])  # cannot read the rules: read nothing
        finally:
            socket.setdefaulttimeout(old)
        _robots[host] = parser
    return _robots[host].can_fetch(USER_AGENT, url)


def get(url: str) -> str | None:
    if not allowed(url):
        print(f"  robots.txt disallows {url}", file=sys.stderr)
        return None
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = resp.read().decode("utf-8", "replace")
            time.sleep(DELAY)
            return body
        except urllib.error.HTTPError as err:
            time.sleep(DELAY)
            if err.code in (429, 500, 502, 503, 504) and attempt < 2:
                time.sleep(5 * (attempt + 1))
                continue
            return None
        except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException):  # incl. ssl errors, resets
            time.sleep(3 * (attempt + 1))
    return None


def json_ld(page: str) -> list:
    out = []
    for block in re.findall(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', page, re.S):
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            continue
        out.extend(data if isinstance(data, list) else [data])
    return out
