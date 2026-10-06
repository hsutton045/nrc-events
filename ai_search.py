from __future__ import annotations

import hashlib
import json
import os
import re
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
from openai import OpenAI

from database import all_events_for_embedding, connect

EMBED_MODEL = os.getenv("OPENAI_EMBED_MODEL", "text-embedding-3-small")
EMBED_DIM = int(os.getenv("OPENAI_EMBED_DIM", "512"))
AI_MODEL = os.getenv("OPENAI_AI_MODEL", "gpt-5.4-mini")
API_TIMEOUT = float(os.getenv("OPENAI_API_TIMEOUT", "90"))
API_MAX_RETRIES = int(os.getenv("OPENAI_API_MAX_RETRIES", "1"))
CLASSIFY_BATCH_SIZE = int(os.getenv("AI_CLASSIFY_BATCH_SIZE", "8"))
CLASSIFY_WORKERS = int(os.getenv("AI_CLASSIFY_WORKERS", "4"))
CLASSIFIER_TEXT_LIMIT = int(os.getenv("AI_CLASSIFIER_TEXT_LIMIT", "10000"))

CLASSIFICATION_FIELDS = [
    "component", "subcomponent", "failure_mode", "failure_cause",
    "failure_mechanism", "initiating_event", "plant_response",
    "automatic_actions", "operator_actions", "redundant_system_response",
    "restoration_action", "event_outcome", "evidence", "rationale",
]
BOOL_FIELDS = [
    "reactor_trip", "power_reduction", "safety_system_actuation",
    "common_cause_indicator", "human_error_indicator", "maintenance_related",
]


def log(message: str) -> None:
    print(message, flush=True)


def client() -> OpenAI:
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is not configured.")
    return OpenAI(timeout=API_TIMEOUT, max_retries=API_MAX_RETRIES)


def content(event: dict, limit: int = 30000) -> str:
    text = (
        f"Title: {event.get('title', '')}\n"
        f"Facility: {event.get('facility', '')}\n"
        f"State: {event.get('state', '')}\n"
        f"Date: {event.get('report_date', '')}\n"
        f"Event:\n{event.get('event_text', '')}"
    )
    return text[:limit]


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def pack(vector) -> bytes:
    return np.asarray(vector, dtype=np.float32).tobytes()


def unpack(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32)


def embed_missing(batch_size: int = 64, force: bool = False) -> int:
    api = client()
    events = all_events_for_embedding()
    todo = []

    with connect() as db:
        for event in events:
            ch = content_hash(content(event))
            row = db.execute(
                "SELECT content_hash, model, dimensions FROM embeddings WHERE event_number=?",
                (event["event_number"],),
            ).fetchone()
            if (
                force
                or not row
                or row["content_hash"] != ch
                or row["model"] != EMBED_MODEL
                or row["dimensions"] != EMBED_DIM
            ):
                todo.append((event, ch))

        if not todo:
            log(f"[EMBED] All {len(events):,} events are already current.")
            return 0

        log(f"[EMBED] {len(todo):,} event(s) require embeddings.")
        done = 0
        for i in range(0, len(todo), batch_size):
            batch = todo[i : i + batch_size]
            response = api.embeddings.create(
                model=EMBED_MODEL,
                dimensions=EMBED_DIM,
                input=[content(event) for event, _ in batch],
            )
            for (event, ch), item in zip(batch, response.data):
                db.execute(
                    """INSERT INTO embeddings(event_number,model,dimensions,vector,content_hash)
                       VALUES(?,?,?,?,?)
                       ON CONFLICT(event_number) DO UPDATE SET
                         model=excluded.model,
                         dimensions=excluded.dimensions,
                         vector=excluded.vector,
                         content_hash=excluded.content_hash,
                         updated_at=CURRENT_TIMESTAMP""",
                    (event["event_number"], EMBED_MODEL, EMBED_DIM, pack(item.embedding), ch),
                )
                done += 1
            db.commit()
            log(f"[EMBED] {done:,}/{len(todo):,} complete")
    return done


def semantic_candidates(query: str, start=None, end=None, limit: int = 100, progress=None) -> list[dict]:
    started = time.perf_counter()
    log(f'[AI SEARCH] Query: "{query}"')
    if progress: progress(stage="embedding", message="Creating query embedding…", percent=3)
    log("[AI SEARCH] Creating query embedding...")
    response = client().embeddings.create(
        model=EMBED_MODEL, dimensions=EMBED_DIM, input=query
    )
    qv = np.asarray(response.data[0].embedding, dtype=np.float32)
    log(f"[AI SEARCH] Query embedding complete ({len(qv)} dimensions).")
    if progress: progress(stage="retrieval", message="Searching the NRC event database…", percent=8)

    sql = """SELECT e.*, b.vector
             FROM events e
             JOIN embeddings b ON b.event_number=e.event_number
             WHERE b.model=? AND b.dimensions=?"""
    params = [EMBED_MODEL, EMBED_DIM]
    if start:
        sql += " AND e.report_date_iso>=?"
        params.append(start)
    if end:
        sql += " AND e.report_date_iso<=?"
        params.append(end)

    with connect() as db:
        rows = db.execute(sql, params).fetchall()

    log(f"[AI SEARCH] Comparing against {len(rows):,} indexed event(s)...")
    if progress: progress(message=f"Comparing against {len(rows):,} indexed NRC events…", indexed=len(rows), percent=12)
    qnorm = np.linalg.norm(qv)
    scored = []
    for row in rows:
        vector = unpack(row["vector"])
        score = float(np.dot(qv, vector) / (qnorm * np.linalg.norm(vector) + 1e-12))
        item = dict(row)
        item.pop("vector", None)
        item["semantic_score"] = score
        scored.append(item)

    result = sorted(scored, key=lambda x: x["semantic_score"], reverse=True)[:limit]
    log(
        f"[AI SEARCH] Selected {len(result)} semantic candidate(s) "
        f"in {time.perf_counter() - started:.1f}s."
    )
    if progress: progress(stage="classification", message=f"Found {len(result):,} candidates. Preparing classification…", candidates=len(result), percent=18)
    return result


def _cache_key(query: str) -> str:
    # Keep the same cache-key algorithm as the first release so existing
    # classifications (including the user's first 42) remain reusable.
    return hashlib.sha256((AI_MODEL + "|" + query.strip().lower()).encode()).hexdigest()


def _extract_json(text: str):
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        first_array, last_array = text.find("["), text.rfind("]")
        if first_array >= 0 and last_array > first_array:
            return json.loads(text[first_array : last_array + 1])
        first_obj, last_obj = text.find("{"), text.rfind("}")
        if first_obj >= 0 and last_obj > first_obj:
            return json.loads(text[first_obj : last_obj + 1])
        raise


def _batch_prompt(query: str, events: list[dict]) -> str:
    payload = [
        {
            "event_number": str(e["event_number"]),
            "event": content(e, CLASSIFIER_TEXT_LIMIT),
        }
        for e in events
    ]
    return f"""You are classifying NRC Event Notification narratives for nuclear-power-plant dependability and digital-twin research.

RESEARCH REQUEST:
{query}

For EACH supplied event, decide whether the event actually satisfies the requested phenomenon, not merely whether related words appear. Be conservative. Distinguish an actual component failure, degradation, trip, loss of function, or requested condition from routine maintenance, testing, contextual mentions, hypothetical conditions, and unrelated failures.

Return ONLY a JSON array. Return exactly one object for every supplied event and preserve its event_number. Each object must contain these keys:
event_number, relevant (boolean), confidence (0..1), component, subcomponent, failure_mode, failure_cause, failure_mechanism, initiating_event, plant_response, automatic_actions, operator_actions, redundant_system_response, reactor_trip (boolean|null), power_reduction (boolean|null), safety_system_actuation (boolean|null), restoration_action, common_cause_indicator (boolean|null), human_error_indicator (boolean|null), maintenance_related (boolean|null), event_outcome, evidence, rationale.

Use null or an empty string when the narrative does not support a field. Do not infer facts that are not stated. confidence is a classification confidence indicator, not a calibrated probability. Keep evidence and rationale brief.

EVENTS:
{json.dumps(payload, ensure_ascii=False)}"""


def _classify_batch(query: str, events: list[dict]) -> list[dict]:
    response = client().responses.create(model=AI_MODEL, input=_batch_prompt(query, events))
    parsed = _extract_json(response.output_text)
    if isinstance(parsed, dict) and "results" in parsed:
        parsed = parsed["results"]
    if not isinstance(parsed, list):
        raise ValueError("Classifier did not return a JSON array.")

    by_number = {str(item.get("event_number", "")): item for item in parsed if isinstance(item, dict)}
    missing = [str(e["event_number"]) for e in events if str(e["event_number"]) not in by_number]
    if missing:
        raise ValueError("Classifier omitted event(s): " + ", ".join(missing))
    return [by_number[str(e["event_number"])] for e in events]


def _text_db(value) -> str:
    """Normalize unpredictable model output into SQLite-safe text."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value)


def _bool_db(value):
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(bool(value))
    if isinstance(value, str):
        v = value.strip().lower()
        if v in {"true", "yes", "1", "y"}:
            return 1
        if v in {"false", "no", "0", "n"}:
            return 0
        if v in {"null", "none", "unknown", "not stated", "n/a"}:
            return None
    return int(bool(value))


def _float_db(value) -> float:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, x))


def _normalize_classification(data: dict, event_number: str) -> dict:
    """Make model output predictable without throwing away the raw response."""
    normalized = dict(data) if isinstance(data, dict) else {}
    normalized["event_number"] = str(event_number)
    normalized["relevant"] = bool(_bool_db(normalized.get("relevant")) or 0)
    normalized["confidence"] = _float_db(normalized.get("confidence"))
    for field in CLASSIFICATION_FIELDS:
        normalized[field] = _text_db(normalized.get(field, ""))
    for field in BOOL_FIELDS:
        db_value = _bool_db(normalized.get(field))
        normalized[field] = None if db_value is None else bool(db_value)
    return normalized


def _save_classification(db, cache_key: str, event_number: str, data: dict) -> None:
    data = _normalize_classification(data, event_number)
    values = [data.get(field, "") for field in CLASSIFICATION_FIELDS]
    db.execute(
        """INSERT OR REPLACE INTO ai_classifications(
             cache_key,event_number,model,relevant,confidence,
             component,subcomponent,failure_mode,failure_cause,failure_mechanism,
             initiating_event,plant_response,automatic_actions,operator_actions,
             redundant_system_response,reactor_trip,power_reduction,
             safety_system_actuation,restoration_action,common_cause_indicator,
             human_error_indicator,maintenance_related,event_outcome,evidence,
             rationale,raw_json)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            cache_key, event_number, AI_MODEL, int(data["relevant"]), data["confidence"],
            *values[:10], _bool_db(data.get("reactor_trip")),
            _bool_db(data.get("power_reduction")),
            _bool_db(data.get("safety_system_actuation")), values[10],
            _bool_db(data.get("common_cause_indicator")),
            _bool_db(data.get("human_error_indicator")),
            _bool_db(data.get("maintenance_related")), *values[11:],
            json.dumps(data, ensure_ascii=False),
        ),
    )


def _classify_with_fallback(query: str, events: list[dict], label: str = "batch"):
    """Classify a batch; on failure recursively split it so one bad response cannot kill the search."""
    try:
        return _classify_batch(query, events), []
    except Exception as exc:
        if len(events) == 1:
            number = str(events[0]["event_number"])
            log(f"[AI SEARCH] {label} event {number} retrying individually after: {exc}")
            try:
                return _classify_batch(query, events), []
            except Exception as final_exc:
                log(f"[AI SEARCH] Event {number} FAILED after retry: {final_exc}")
                return [], [{"event_number": number, "error": str(final_exc)}]
        mid = len(events) // 2
        log(f"[AI SEARCH] {label} failed ({exc}); splitting {len(events)} events into smaller batches.")
        left, left_errors = _classify_with_fallback(query, events[:mid], label + "A")
        right, right_errors = _classify_with_fallback(query, events[mid:], label + "B")
        return left + right, left_errors + right_errors

def classify(query: str, events: list[dict], max_events: int = 80, return_status: bool = False, progress=None):
    started = time.perf_counter()
    selected = events[:max_events]
    cache_key = _cache_key(query)
    cached = {}

    with connect() as db:
        for event in selected:
            row = db.execute(
                "SELECT * FROM ai_classifications WHERE cache_key=? AND event_number=?",
                (cache_key, event["event_number"]),
            ).fetchone()
            if row:
                cached[str(event["event_number"])] = dict(row)

    missing = [e for e in selected if str(e["event_number"]) not in cached]
    log(f"[AI SEARCH] Classification cache: {len(cached)} hit(s), {len(missing)} event(s) require AI.")
    if progress:
        progress(stage="classification", message=f"{len(cached):,} cached; {len(missing):,} require AI classification.", requested=len(selected), cache_hits=len(cached), completed=len(cached), relevant=sum(bool(x.get("relevant")) for x in cached.values()), percent=20 + int(75 * len(cached) / max(1, len(selected))))
    batches = [missing[i:i + CLASSIFY_BATCH_SIZE] for i in range(0, len(missing), CLASSIFY_BATCH_SIZE)]
    newly_classified = {}
    failures = []

    if batches:
        workers = max(1, min(CLASSIFY_WORKERS, len(batches)))
        log(f"[AI SEARCH] Classifying {len(missing)} event(s) in {len(batches)} batch(es) "
            f"of up to {CLASSIFY_BATCH_SIZE}, using {workers} worker(s)...")
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(_classify_with_fallback, query, batch, f"Batch {index}/{len(batches)}"): (index, batch)
                for index, batch in enumerate(batches, start=1)
            }
            completed = 0
            for future in as_completed(futures):
                index, batch = futures[future]
                try:
                    results, batch_failures = future.result()
                    normalized = []
                    for item in results:
                        number = str(item.get("event_number", ""))
                        if number:
                            clean = _normalize_classification(item, number)
                            newly_classified[number] = clean
                            normalized.append(clean)
                    if normalized:
                        with connect() as db:
                            for item in normalized:
                                _save_classification(db, cache_key, item["event_number"], item)
                            db.commit()
                    failures.extend(batch_failures)
                    completed += len(normalized)
                    relevant = sum(bool(x.get("relevant")) for x in normalized)
                    suffix = f", {len(batch_failures)} failed" if batch_failures else ""
                    log(f"[AI SEARCH] Batch {index}/{len(batches)} complete: {len(normalized)} classified, "
                        f"{relevant} relevant{suffix} ({completed}/{len(missing)} new complete).")
                    if progress:
                        total_done = len(cached) + completed
                        relevant_so_far = sum(bool(x.get("relevant")) for x in cached.values()) + sum(bool(x.get("relevant")) for x in newly_classified.values())
                        progress(stage="classification", message=f"Classifying events… {total_done:,} / {len(selected):,}", requested=len(selected), cache_hits=len(cached), completed=total_done, relevant=relevant_so_far, failed=len(failures), percent=20 + int(75 * total_done / max(1, len(selected))))
                except Exception as exc:
                    numbers = [str(e["event_number"]) for e in batch]
                    failures.extend({"event_number": n, "error": str(exc)} for n in numbers)
                    log(f"[AI SEARCH] Batch {index}/{len(batches)} unexpectedly FAILED: {exc}")

    output = []
    for event in selected:
        number = str(event["event_number"])
        data = cached.get(number) or newly_classified.get(number)
        if data is None:
            continue
        merged = dict(data)
        merged.update(event)
        output.append(merged)

    relevant_count = sum(bool(x.get("relevant")) for x in output)
    log(f"[AI SEARCH] Classification complete: {relevant_count} relevant / {len(output)} classified "
        f"in {time.perf_counter() - started:.1f}s.")
    if failures:
        log(f"[AI SEARCH] WARNING: {len(failures)} event(s) could not be classified; successful results were retained.")

    if progress:
        progress(stage="finalizing", message="Finalizing search results…", completed=len(output), relevant=relevant_count, failed=len(failures), percent=98)

    status = {
        "requested": len(selected), "classified": len(output), "failed": len(failures),
        "failures": failures, "cache_hits": len(cached), "new_classifications": len(newly_classified),
    }
    return (output, status) if return_status else output

def stats(items: list[dict]) -> dict:
    relevant = [x for x in items if bool(x.get("relevant"))]

    def count(field):
        return Counter((x.get(field) or "Unknown").strip() or "Unknown" for x in relevant)

    def true_count(field):
        return sum(1 for x in relevant if x.get(field) in (True, 1))

    return {
        "total_candidates": len(items),
        "total_relevant": len(relevant),
        "excluded": len(items) - len(relevant),
        "failure_modes": count("failure_mode").most_common(),
        "components": count("component").most_common(),
        "causes": count("failure_cause").most_common(),
        "reactor_trips": true_count("reactor_trip"),
        "power_reductions": true_count("power_reduction"),
        "safety_actuations": true_count("safety_system_actuation"),
    }


def generate_report(query: str, relevant: list[dict], statistics: dict) -> str:
    log(f"[AI REPORT] Generating report from {len(relevant)} relevant event(s)...")
    started = time.perf_counter()
    compact = [
        {
            key: event.get(key)
            for key in (
                "event_number", "report_date", "facility", "state", "component",
                "failure_mode", "failure_cause", "plant_response", "reactor_trip", "evidence",
            )
        }
        for event in relevant
    ]
    prompt = f"""Write a concise research analysis of NRC Event Notification results for: {query}.
The audience is developing nuclear-plant digital twins, agent-based models, and/or Petri-net dependability models. Use ONLY the supplied classified results and deterministic statistics. Do not invent failure rates, probabilities, exposure hours, or population-level reliability claims. Clearly distinguish counts within this retrieved NRC event set from component failure probability. Discuss recurring failure modes, causes, plant responses, possible state transitions/causal chains, modeling implications, and limitations. Cite event numbers inline as evidence.

STATISTICS:
{json.dumps(statistics)}

EVENTS:
{json.dumps(compact)}"""
    result = client().responses.create(model=AI_MODEL, input=prompt).output_text
    log(f"[AI REPORT] Complete in {time.perf_counter() - started:.1f}s.")
    return result
