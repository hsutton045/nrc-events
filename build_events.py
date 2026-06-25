from __future__ import annotations

import argparse
import json
import re
import time
from datetime import datetime, date
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.nrc.gov"
YEAR_INDEX_TEMPLATE = "https://www.nrc.gov/reading-rm/doc-collections/event-status/event/{year}/index.html"

DATA_DIR = Path("data")
OUT_FILE = DATA_DIR / "events.json"
LAST_UPDATED_FILE = DATA_DIR / "last_updated.txt"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Python NRCEventSearch/1.0"
}


def fetch_html(url: str) -> str:
    response = requests.get(url, headers=HEADERS, timeout=(10, 30))
    response.raise_for_status()
    return response.text


def clean_text(text: str) -> str:
    text = text.replace("\xa0", " ")
    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def parse_report_date(date_str: str) -> date | None:
    if not date_str:
        return None

    for fmt in (
        "%B %d, %Y",
        "%b %d, %Y",
        "%m/%d/%Y",
        "%m-%d-%Y",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(date_str.strip(), fmt).date()
        except ValueError:
            continue
    return None


def load_existing_events() -> list[dict]:
    if not OUT_FILE.exists():
        return []

    with open(OUT_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list):
        return []

    return data


def get_latest_saved_date(events: list[dict]) -> date | None:
    latest = None

    for event in events:
        d = parse_report_date(str(event.get("report_date", "")))
        if d is None:
            continue
        if latest is None or d > latest:
            latest = d

    return latest


def get_daily_links(year: int) -> list[str]:
    html = fetch_html(YEAR_INDEX_TEMPLATE.format(year=year))
    soup = BeautifulSoup(html, "html.parser")

    links = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()

        if re.search(r"\d{8}en(?:\.html)?$", href):
            full_url = urljoin(YEAR_INDEX_TEMPLATE.format(year=year), href)
            links.append(full_url)

    seen = set()
    unique = []
    for link in links:
        if link not in seen:
            seen.add(link)
            unique.append(link)

    return unique

def report_date_from_link(url: str) -> date | None:
    m = re.search(r"/(\d{8})en(?:\.html)?$", url)
    if not m:
        return None

    try:
        return datetime.strptime(m.group(1), "%Y%m%d").date()
    except ValueError:
        return None


def filter_links_since(links: list[str], since_date: date | None) -> list[str]:
    if since_date is None:
        return links

    filtered = []
    for link in links:
        d = report_date_from_link(link)
        if d is None or d >= since_date:
            filtered.append(link)

    return filtered


def extract_report_date(page_text: str) -> str:
    m = re.search(r"Event Notification Report for ([A-Za-z]+ \d{1,2}, \d{4})", page_text)
    return m.group(1) if m else ""


def split_event_blocks(page_text: str) -> list[str]:
    starts = [m.start() for m in re.finditer(r"(?m)^Event Number:\s*", page_text)]
    if not starts:
        return []

    blocks = []
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else len(page_text)
        blocks.append(page_text[start:end].strip())

    return blocks


def extract_field(pattern: str, text: str, flags: int = 0) -> str:
    m = re.search(pattern, text, flags)
    if not m:
        return ""
    return clean_text(m.group(1))


def best_title_from_block(block: str, fallback_title: str = "") -> str:
    event_text = extract_field(r"Event Text\s*(.*)", block, re.DOTALL)
    if event_text:
        lines = [line.strip() for line in event_text.splitlines() if line.strip()]
        for line in lines[:8]:
            if len(line) <= 160:
                return line

    return fallback_title.strip()


def parse_daily_page(url: str) -> list[dict]:
    html = fetch_html(url)
    soup = BeautifulSoup(html, "html.parser")
    page_text = clean_text(soup.get_text("\n"))

    report_date = extract_report_date(page_text)
    blocks = split_event_blocks(page_text)

    events = []
    for block in blocks:
        event_number = extract_field(r"Event Number:\s*(.+)", block)
        facility = extract_field(r"Facility:\s*(.+)", block)
        state = extract_field(r"State:\s*([A-Z]{2})", block)
        title = extract_field(r"Notification Date:.*?\n(.*?)\n", block, re.DOTALL)
        event_text = extract_field(r"Event Text\s*(.*)", block, re.DOTALL)

        if not event_number:
            continue

        display_title = best_title_from_block(block, fallback_title=title)

        events.append({
            "event_number": str(event_number).strip(),
            "report_date": report_date,
            "facility": facility,
            "state": state,
            "title": display_title,
            "event_text": event_text,
            "report_url": url,
        })

    return events


def build_events(years: list[int], since_date: date | None = None) -> list[dict]:
    all_events = []

    for year in years:
        print(f"\nGetting daily links for {year}...")
        links = get_daily_links(year)
        links = filter_links_since(links, since_date)
        print(f"Found {len(links)} daily report pages to scan")

        for i, link in enumerate(links, start=1):
            print(f"[{year} {i}/{len(links)}] {link}")
            try:
                events = parse_daily_page(link)
                all_events.extend(events)
            except Exception as exc:
                print(f"Error parsing {link}: {exc}")
            time.sleep(0.5)

    deduped = {}
    for event in all_events:
        deduped[str(event["event_number"])] = event

    return list(deduped.values())


def merge_events(existing: list[dict], new_events: list[dict]) -> list[dict]:
    merged = {}

    for event in existing:
        event_number = str(event.get("event_number", "")).strip()
        if event_number:
            merged[event_number] = event

    for event in new_events:
        event_number = str(event.get("event_number", "")).strip()
        if event_number:
            merged[event_number] = event

    def sort_key(event: dict):
        d = parse_report_date(str(event.get("report_date", "")))
        return d or date.min

    return sorted(merged.values(), key=sort_key, reverse=True)


def save_events(events: list[dict]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    temp_file = OUT_FILE.with_suffix(".tmp")

    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(events, f, indent=2, ensure_ascii=False)

    temp_file.replace(OUT_FILE)


def write_last_updated() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(LAST_UPDATED_FILE, "w", encoding="utf-8") as f:
        f.write(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))


def parse_args():
    parser = argparse.ArgumentParser(description="Build or update NRC event database")
    parser.add_argument(
        "--full",
        action="store_true",
        help="Ignore saved latest date and rebuild from all selected years",
    )
    parser.add_argument(
        "--start-year",
        type=int,
        default=1999,
        help="First year to scan for --full mode or manual runs",
    )
    parser.add_argument(
        "--end-year",
        type=int,
        default=datetime.now().year,
        help="Last year to scan",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()

    existing_events = load_existing_events()
    latest_saved_date = get_latest_saved_date(existing_events)

    print(f"Loaded {len(existing_events)} existing events")
    print(f"Latest saved report date: {latest_saved_date}")

    years_to_scan = list(range(args.start_year, args.end_year + 1))

    if args.full:
        print("Running FULL rebuild mode")
        since_date = None
        base_events = []
    else:
        print("Running incremental update mode")
        since_date = latest_saved_date
        base_events = existing_events

        if not years_to_scan:
            years_to_scan = [datetime.now().year, datetime.now().year - 1]

    new_events = build_events(years_to_scan, since_date=since_date)
    merged_events = merge_events(base_events, new_events)

    save_events(merged_events)
    write_last_updated()

    print(f"\nFetched/updated {len(new_events)} events")
    print(f"Saved {len(merged_events)} total events to {OUT_FILE}")
    print(f"Wrote timestamp to {LAST_UPDATED_FILE}")
