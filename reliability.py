"""Reference catalog and optional user-verified reliability parameter dataset.
No parameters are scraped, estimated, or fabricated.
"""
import csv, os
from pathlib import Path

SOURCES = [
    {"name":"NRC Reactor Operational Experience (INL)", "url":"https://nrcoe.inl.gov/", "description":"Industry-average component performance and operational experience."},
    {"name":"NRC Industry-Average Parameter Estimates", "url":"https://nrcoe.inl.gov/avgperf/", "description":"Published PRA component reliability parameter estimates; check component, failure mode, units, period and uncertainty."},
    {"name":"NRC Emergency Power System Studies", "url":"https://nrcoe.inl.gov/SysStudy/EPS", "description":"Emergency power system reliability studies and related EDG operating experience."},
]

def load_parameters():
    path = os.getenv("NRC_RELIABILITY_CSV", "").strip()
    if not path: return [], "No verified reliability parameter file configured."
    if not Path(path).is_file(): return [], "Configured reliability parameter file not found."
    required = {"component", "failure_mode", "parameter_type", "value", "unit", "source_url", "study_period", "population"}
    rows=[]
    try:
        with open(path, newline="", encoding="utf-8-sig") as f:
            reader=csv.DictReader(f)
            if not required.issubset(reader.fieldnames or []):
                return [], "Reliability CSV is missing required columns: " + ", ".join(sorted(required-set(reader.fieldnames or [])))
            for row in reader:
                if not any((v or "").strip() for v in row.values()): continue
                try: value=float(row["value"])
                except (TypeError,ValueError): continue
                if value < 0 or not row["source_url"].startswith("https://"): continue
                clean={k:(v or "").strip() for k,v in row.items() if k}
                clean["value"]=value
                rows.append(clean)
    except (OSError,UnicodeError,csv.Error) as exc:
        return [], "Unable to read reliability CSV: " + str(exc)
    return rows, None

def relevant_parameters(query, events, parameters, limit=12):
    """Suggest matching reference parameters; do not silently assign rates to events."""
    terms=set(query.lower().split())
    for e in events:
        terms.update(str(e.get("component") or "").lower().split())
    scored=[]
    for p in parameters:
        name=p["component"].lower()
        tokens=set(name.split())
        score=len(terms & tokens)
        if score: scored.append((score,p))
    return [p for _,p in sorted(scored,key=lambda x:-x[0])[:limit]]
