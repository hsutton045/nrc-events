from __future__ import annotations

import html
import json
import os
import re
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

from flask import Flask, abort, render_template, request

DATA_FILE = Path("data/events.json")
LAST_UPDATED_FILE = Path("data/last_updated.txt")
BUILD_SCRIPT = Path("build_events.py")
UPDATE_LOCK_FILE = Path("data/update.lock")
UPDATE_INTERVAL = timedelta(days=1)

app = Flask(__name__)


def load_events() -> list[dict]:
    if not DATA_FILE.exists():
        return []
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def parse_report_date(date_str: str):
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


def parse_html_date(date_str: str):
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str.strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def parse_terms(text: str) -> list[str]:
    return [term.strip().lower() for term in text.split() if term.strip()]


def build_search_text(event: dict) -> str:
    return " ".join([
        str(event.get("event_number", "")),
        str(event.get("facility", "")),
        str(event.get("state", "")),
        str(event.get("title", "")),
        str(event.get("event_text", "")),
    ]).lower()


def build_report_url(event: dict) -> str:
    event_date = parse_report_date(event.get("report_date", ""))
    if event_date is None:
        return event.get("report_url", "")

    year = event_date.strftime("%Y")
    ymd = event_date.strftime("%Y%m%d")
    return f"https://www.nrc.gov/reading-rm/doc-collections/event-status/event/{year}/{ymd}en"


def keyword_match(event: dict, keywords: str) -> bool:
    if not keywords.strip():
        return True

    haystack = build_search_text(event)
    terms = parse_terms(keywords)
    return all(term in haystack for term in terms)


def exclude_match(event: dict, exclude_keywords: str) -> bool:
    if not exclude_keywords.strip():
        return False

    haystack = build_search_text(event)
    terms = parse_terms(exclude_keywords)
    return any(term in haystack for term in terms)


def date_in_range(event: dict, start_date, end_date) -> bool:
    if start_date is None and end_date is None:
        return True

    event_date = parse_report_date(event.get("report_date", ""))
    if event_date is None:
        return False

    if start_date is not None and event_date < start_date:
        return False

    if end_date is not None and event_date > end_date:
        return False

    return True


def clean_event_text(text: str) -> str:
    if not text:
        return ""

    text = text.replace("<br><br>", "\n\n")
    text = text.replace("<br />", "\n")
    text = text.replace("<br/>", "\n")
    text = text.replace("<br>", "\n")
    text = html.unescape(text)

    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)

    return text.strip()


def display_title(event: dict) -> str:
    text = clean_event_text(event.get("event_text", ""))
    lines = [line.strip() for line in text.splitlines() if line.strip()]

    for line in lines[:8]:
        if len(line) <= 140:
            return line

    return str(event.get("title", "Event")).strip() or "Event"


def make_keyword_snippet(text: str, keywords: str, radius: int = 120) -> str:
    text = clean_event_text(text)
    flat = re.sub(r"\s+", " ", text).strip()

    if not flat:
        return ""

    include_terms = parse_terms(keywords)

    match_start = None
    match_end = None

    for term in include_terms:
        match = re.search(re.escape(term), flat, flags=re.IGNORECASE)
        if match:
            match_start = match.start()
            match_end = match.end()
            break

    if match_start is None:
        if len(flat) <= radius * 2:
            return flat
        return flat[: radius * 2].rstrip() + "..."

    start = max(0, match_start - radius)
    end = min(len(flat), match_end + radius)

    snippet = flat[start:end]

    if start > 0:
        snippet = "..." + snippet
    if end < len(flat):
        snippet = snippet + "..."

    return snippet


def parse_last_updated() -> datetime | None:
    if not LAST_UPDATED_FILE.exists():
        return None

    try:
        text = LAST_UPDATED_FILE.read_text(encoding="utf-8").strip()
        return datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


def get_last_updated_string() -> str:
    last_updated = parse_last_updated()
    if last_updated is None:
        return "Never"
    return last_updated.strftime("%Y-%m-%d %H:%M:%S")


def needs_update() -> bool:
    last_updated = parse_last_updated()
    if last_updated is None:
        return True
    return datetime.now() - last_updated > UPDATE_INTERVAL


def is_update_in_progress() -> bool:
    return UPDATE_LOCK_FILE.exists()


def maybe_update_events() -> None:
    """
    If data is older than UPDATE_INTERVAL, run build_events.py.
    Uses a lock file so overlapping requests do not launch multiple updates.
    """
    if not needs_update():
        return

    if is_update_in_progress():
        return

    UPDATE_LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)

    try:
        UPDATE_LOCK_FILE.write_text(
            f"started {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n",
            encoding="utf-8",
        )

        if not needs_update():
            return

        print("Event data is stale. Running incremental update...")

        result = subprocess.run(
            ["python3", str(BUILD_SCRIPT)],
            cwd=Path(__file__).parent,
            capture_output=True,
            text=True,
        )

        print(result.stdout)
        if result.returncode != 0:
            print("build_events.py failed:")
            print(result.stderr)

    finally:
        try:
            UPDATE_LOCK_FILE.unlink(missing_ok=True)
        except Exception:
            pass


@app.route("/")
def index():
    maybe_update_events()
    events = load_events()

    keywords = request.args.get("keywords", "").strip()
    exclude_keywords = request.args.get("exclude_keywords", "").strip()
    start = request.args.get("start_date", "").strip()
    end = request.args.get("end_date", "").strip()

    start_date = parse_html_date(start)
    end_date = parse_html_date(end)

    results = []
    if keywords or exclude_keywords or start_date or end_date:
        results = [
            dict(event)
            for event in events
            if keyword_match(event, keywords)
            and not exclude_match(event, exclude_keywords)
            and date_in_range(event, start_date, end_date)
        ]

        for event in results:
            event["display_title"] = display_title(event)
            event["snippet"] = make_keyword_snippet(event.get("event_text", ""), keywords)
            event["resolved_report_url"] = build_report_url(event)

        results.sort(
            key=lambda event: parse_report_date(event.get("report_date", "")) or datetime.min.date(),
            reverse=True,
        )

    return render_template(
        "index.html",
        keywords=keywords,
        exclude_keywords=exclude_keywords,
        start_date=start,
        end_date=end,
        results=results,
        last_updated=get_last_updated_string(),
    )


@app.route("/event/<event_number>")
def event_detail(event_number: str):
    events = load_events()

    keywords = request.args.get("keywords", "").strip()
    exclude_keywords = request.args.get("exclude_keywords", "").strip()
    start_date = request.args.get("start_date", "").strip()
    end_date = request.args.get("end_date", "").strip()

    for event in events:
        if str(event.get("event_number")) == str(event_number):
            event = dict(event)
            event["formatted_text"] = clean_event_text(event.get("event_text", ""))
            event["display_title"] = display_title(event)
            event["resolved_report_url"] = build_report_url(event)

            return render_template(
                "event.html",
                event=event,
                keywords=keywords,
                exclude_keywords=exclude_keywords,
                start_date=start_date,
                end_date=end_date,
                last_updated=get_last_updated_string(),
            )

    abort(404)


if __name__ == "__main__":
    app.run(debug=True)
