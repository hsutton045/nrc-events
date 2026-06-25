from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import List

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.nrc.gov"
YEAR_URL_TEMPLATE = "https://www.nrc.gov/reading-rm/doc-collections/event-status/event/{year}/index"
DATA_FILE = Path("data/events.json")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; NRCEventSearch/1.0)"
}


@dataclass
class NRCEvent:
    event_number: str
    report_date: str
    facility: str
    state: str
    title: str
    event_text: str
    report_url: str


def fetch_html(url: str) -> str:
    response = requests.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    return response.text


def clean_text(text: str) -> str:
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s+", "\n", text)
    return text.strip()


def get_daily_links(year: int) -> List[str]:
    html = fetch_html(YEAR_URL_TEMPLATE.format(year=year))
    soup = BeautifulSoup(html, "html.parser")

    links = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if re.search(rf"/reading-rm/doc-collections/event-status/event/{year}/\d{{8}}en$", href):
            full = href if href.startswith("http") else BASE_URL + href
            links.append(full)

    return list(dict.fromkeys(links))


def extract_report_date(page_text: str) -> str:
    m = re.search(r"Event Notification Report for ([A-Za-z]+ \d{1,2}, \d{4})", page_text)
    return m.group(1) if m else ""


def split_blocks(page_text: str) -> List[str]:
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
    return clean_text(m.group(1)) if m else ""


def parse_daily_page(url: str) -> List[NRCEvent]:
    html = fetch_html(url)
    soup = BeautifulSoup(html, "html.parser")
    page_text = clean_text(soup.get_text("\n"))

    report_date = extract_report_date(page_text)
    blocks = split_blocks(page_text)

    events = []
    for block in blocks:
        event_number = extract_field(r"Event Number:\s*(.+)", block)
        facility = extract_field(r"Facility:\s*(.+)", block)
        state = extract_field(r"State:\s*([A-Z]{2})", block)
        event_text = extract_field(r"Event Text\s*(.*)", block, re.DOTALL)

        lines = [line.strip() for line in event_text.splitlines() if line.strip()]
        title = lines[0] if lines else ""

        events.append(
            NRCEvent(
                event_number=event_number,
                report_date=report_date,
                facility=facility,
                state=state,
                title=title,
                event_text=event_text,
                report_url=url,
            )
        )

    return events


def build_database(years: List[int]) -> List[dict]:
    all_events = []

    for year in years:
        links = get_daily_links(year)
        for i, link in enumerate(links, start=1):
            print(f"[{year}] {i}/{len(links)} {link}")
            try:
                events = parse_daily_page(link)
                all_events.extend(asdict(e) for e in events)
            except Exception as e:
                print(f"Error parsing {link}: {e}")
            time.sleep(1)

    return all_events


def save_database(events: List[dict]) -> None:
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(events, f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    years = [2026, 2025]
    events = build_database(years)
    save_database(events)
    print(f"Saved {len(events)} events to {DATA_FILE}")
