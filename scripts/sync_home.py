#!/usr/bin/env python3
import copy
import datetime as dt
import json
import re
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

HOME_URL = "https://home.dk/salg/lejligheder/strandlodsvej-25d-2th-2300-koebenhavn-s/sag-1450004513/"
SITE_URL = "https://strandlodsvej25d.dk/"
INDEX_PATH = Path("index.html")
SITEMAP_PATH = Path("sitemap.xml")
AREA_M2 = 107
TZ = ZoneInfo("Europe/Copenhagen")

MONTHS = {
    "januar": 1,
    "februar": 2,
    "marts": 3,
    "april": 4,
    "maj": 5,
    "juni": 6,
    "juli": 7,
    "august": 8,
    "september": 9,
    "oktober": 10,
    "november": 11,
    "december": 12,
}

OPEN_HOUSE_RE = re.compile(
    r"Åbent hus(?: med tilmelding)?\s+"
    r"([A-Za-zÆØÅæøå]+)\s+(?:d\.\s*)?"
    r"(\d{1,2})\.\s+([A-Za-zÆØÅæøå]+)\s+"
    r"kl\.\s*(\d{1,2})[.:](\d{2})\s*[-–]\s*"
    r"(\d{1,2})[.:](\d{2})",
    re.IGNORECASE,
)

PRICE_RE = re.compile(r"Kontant\s+([0-9.]+)\s*kr\.", re.IGNORECASE)

JSON_LD_RE = re.compile(
    r'(<script\s+type=["\']application/ld\+json["\']\s*>\s*)'
    r'(\{.*?\})'
    r'(\s*</script>)',
    re.IGNORECASE | re.DOTALL,
)

BANNER_RE = re.compile(
    r'\n\s*<div class="open-house-banner">.*?</div>\s*'
    r'\n\s*<div class="hero-actions">',
    re.DOTALL,
)

PRICE_CARD_RE = re.compile(
    r'(<div class="price-card">\s*'
    r'<span>Kontantpris</span>\s*'
    r'<strong>).*?(</strong>\s*'
    r'<small>).*?(</small>)',
    re.DOTALL,
)


def fetch_home():
    request = urllib.request.Request(
        HOME_URL,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/140 Safari/537.36"
            ),
            "Accept-Language": "da-DK,da;q=0.9,en;q=0.7",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        if response.status != 200:
            raise RuntimeError(f"home.dk returned HTTP {response.status}")
        return response.read().decode("utf-8", errors="replace")


def visible_text(page_html):
    soup = BeautifulSoup(page_html, "html.parser")
    return " ".join(soup.stripped_strings)


def extract_price(text):
    match = PRICE_RE.search(text)
    if not match:
        raise RuntimeError("Could not find the cash price on home.dk")
    return int(match.group(1).replace(".", ""))


def infer_year(month, day, today):
    candidate = dt.date(today.year, month, day)
    if candidate < today - dt.timedelta(days=14):
        candidate = dt.date(today.year + 1, month, day)
    return candidate.year


def extract_open_house(text, today):
    match = OPEN_HOUSE_RE.search(text)
    if not match:
        return None

    weekday = match.group(1).capitalize()
    day = int(match.group(2))
    month_name = match.group(3).lower()
    month = MONTHS.get(month_name)
    if not month:
        raise RuntimeError(f"Unknown Danish month from home.dk: {month_name}")

    start_hour = int(match.group(4))
    start_minute = int(match.group(5))
    end_hour = int(match.group(6))
    end_minute = int(match.group(7))
    year = infer_year(month, day, today)

    start = dt.datetime(year, month, day, start_hour, start_minute, tzinfo=TZ)
    end = dt.datetime(year, month, day, end_hour, end_minute, tzinfo=TZ)

    return {
        "weekday": weekday,
        "day": day,
        "month_name": month_name,
        "start": start,
        "end": end,
    }


def format_number(value):
    return f"{value:,}".replace(",", ".")


def event_object(open_house):
    start = open_house["start"]
    end = open_house["end"]
    date_id = start.date().isoformat()
    return {
        "@type": "Event",
        "@id": f"{SITE_URL}#open-house-{date_id}",
        "name": "Åbent hus – Strandlodsvej 25D, 2. th.",
        "startDate": start.isoformat(),
        "endDate": end.isoformat(),
        "eventAttendanceMode": "https://schema.org/OfflineEventAttendanceMode",
        "eventStatus": "https://schema.org/EventScheduled",
        "location": {
            "@type": "Place",
            "name": "Strandlodsvej 25D, 2. th.",
            "address": {
                "@type": "PostalAddress",
                "streetAddress": "Strandlodsvej 25D, 2. th.",
                "postalCode": "2300",
                "addressLocality": "København S",
                "addressCountry": "DK",
            },
        },
        "url": HOME_URL,
    }


def update_json_ld(page, price, open_house, today):
    match = JSON_LD_RE.search(page)
    if not match:
        raise RuntimeError("Could not find JSON-LD block in index.html")

    data = json.loads(match.group(2))
    graph = data.get("@graph")
    if not isinstance(graph, list):
        raise RuntimeError("JSON-LD @graph is missing")

    before = copy.deepcopy(graph)

    listing = next(
        (item for item in graph if item.get("@type") == "RealEstateListing"),
        None,
    )
    if listing is None:
        raise RuntimeError("RealEstateListing JSON-LD object is missing")

    offers = listing.setdefault("offers", {})
    offers["price"] = str(price)
    offers["priceCurrency"] = "DKK"
    offers["url"] = HOME_URL

    graph[:] = [item for item in graph if item.get("@type") != "Event"]
    if open_house:
        graph.append(event_object(open_house))

    if graph != before:
        listing["dateModified"] = today.isoformat()

    rendered = json.dumps(data, ensure_ascii=False, indent=2)
    rendered = rendered.replace("\n", "\n  ")
    return page[: match.start()] + match.group(1) + rendered + match.group(3) + page[match.end() :]


def render_banner(open_house):
    if not open_house:
        return ""

    start = open_house["start"]
    end = open_house["end"]
    label = (
        f'{open_house["weekday"]} {open_house["day"]}. '
        f'{open_house["month_name"]} · '
        f'{start:%H.%M}–{end:%H.%M}'
    )

    return f'''          <div class="open-house-banner">
            <div class="open-house-icon" aria-hidden="true">{open_house["day"]}</div>
            <div class="open-house-copy">
              <span>Åbent hus med tilmelding</span>
              <strong>{label}</strong>
            </div>
            <a href="{HOME_URL}" rel="noopener">
              Tilmeld →
            </a>
          </div>

'''


def update_banner(page, open_house):
    hero_marker = '          <div class="hero-actions">'
    replacement = "\n" + render_banner(open_house) + hero_marker

    if BANNER_RE.search(page):
        return BANNER_RE.sub(replacement, page, count=1)

    if open_house and hero_marker in page:
        return page.replace(hero_marker, render_banner(open_house) + hero_marker, 1)

    return page


def update_price_card(page, price):
    sqm_price = round(price / AREA_M2)
    # Use a callback: a price starting with a digit must not turn \\1 into \\17.
    def render(match):
        return (
            f"{match.group(1)}{format_number(price)} kr."
            f"{match.group(2)}{format_number(sqm_price)} kr./m²"
            f"{match.group(3)}"
        )

    updated, count = PRICE_CARD_RE.subn(render, page, count=1)
    if count != 1:
        raise RuntimeError("Could not find the visible price card in index.html")
    return updated


def update_sitemap(today):
    if not SITEMAP_PATH.exists():
        return
    original = SITEMAP_PATH.read_text(encoding="utf-8")
    updated, count = re.subn(
        r"<lastmod>[^<]+</lastmod>",
        f"<lastmod>{today.isoformat()}</lastmod>",
        original,
        count=1,
    )
    if count == 1 and updated != original:
        SITEMAP_PATH.write_text(updated, encoding="utf-8")


def main():
    now = dt.datetime.now(TZ)
    today = now.date()

    source_html = fetch_home()
    text = visible_text(source_html)
    price = extract_price(text)
    open_house = extract_open_house(text, today)

    # The 4 October 2026 open house was cancelled.
    # Suppress only that date; later open houses can publish automatically.
    if open_house and open_house["start"].date() == dt.date(2026, 10, 4):
        print("Ignoring cancelled open house on 2026-10-04.")
        open_house = None

    if open_house is None:
        # Diagnose whether Home exposes the event in the retrieved HTML.
        for needle in ("4. oktober", "04. oktober", "2026-10-04", "åbent hus", "openhouse"):
            match = re.search(re.escape(needle), source_html, re.IGNORECASE)
            if match:
                excerpt = source_html[max(0, match.start() - 80):match.end() + 160]
                print(f"Home source contains {needle!r}: {excerpt[:260]!r}")
            else:
                print(f"Home source has no occurrence of {needle!r}")
    # Never publish an open house after its end time, even if home.dk is slow to remove it.
    if open_house and open_house["end"] <= now:
        open_house = None

    original = INDEX_PATH.read_text(encoding="utf-8")
    updated = update_json_ld(original, price, open_house, today)
    updated = update_banner(updated, open_house)
    updated = update_price_card(updated, price)

    if updated == original:
        print("No listing changes found.")
        return

    INDEX_PATH.write_text(updated, encoding="utf-8")
    update_sitemap(today)

    if open_house:
        print(
            "Updated from home.dk:",
            format_number(price),
            "DKK;",
            open_house["start"].isoformat(),
            "to",
            open_house["end"].isoformat(),
        )
    else:
        print("Updated from home.dk:", format_number(price), "DKK; no open house listed.")


if __name__ == "__main__":
    main()
