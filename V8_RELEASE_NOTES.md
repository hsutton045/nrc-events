# NRC Research Platform v8 — release and test guide

## Changes
- Added `/api/reliability/match-event?component=edg&failure_mode=FTS` for conservative, explainable matching of classified NRC events to verified published reliability parameter candidates.
- Displays a separate event-to-parameter matching table when AI Search has relevant classified events. An unmatched result is deliberate if classification is ambiguous.
- Retains v7 independent reliability library, review workflow, bulk importer, AnyLogic JSON/CSV and existing event database.
- Updates PyMuPDF import style to avoid deprecated `fitz` warning where applicable.
- Ships `test_v8.py` for isolated regression testing. No SQLite database or uploaded report is bundled.

## Safe installation on Fedora / Fish
1. Back up your project and SQLite database. Preserve `data/`, `.venv/`, `.env`, and any Render persistent disk.
2. Extract `nrc_v8/` from ZIP and copy **its contents** over your local application code. Do not overwrite local data or environment files.
3. `source .venv/bin/activate.fish`
4. `pip install -r requirements.txt`
5. `python test_v8.py` (uses an isolated temporary database; does not alter your existing one).
6. `python test_v7.py` (also isolated).
7. `python test_v6.py ~/Downloads/AvgPara2020.pdf` (isolated, if source PDF available).
8. `python app.py` and browse `/`, `/reliability`, `/review`.
9. Try `/api/reliability/match-event?component=edg&failure_mode=FTS`; if your local database contains the EDG data, expect one verified match.
10. Try `/api/reliability/match-event?component=rcp&failure_mode=FTS`; expect no automatic substitution from generic motor-driven pumps.
11. In the library, export AnyLogic JSON and CSV and verify Beta shape parameters and Gamma shape/scale, correct per-demand/per-hour units, and null repair data.
12. With a populated NRC event DB, search EDG events and check the event-to-parameter candidate section. With an empty local event DB, zero event candidates is expected; reliability library remains functional.

## Scope and safety
- This release does **not** automatically map arbitrary free-text event descriptions to plant-specific rates. Only explicit recognized component aliases and failure modes match.
- Event counts are never treated as exposure-normalized rates.
- Published industry-average parameters are candidates for applicability assessment, not universal plant-specific values.
- v8 retains v6 table extraction coverage; it does **not** claim complete extraction of the 235-page report.
- Flask job state is in memory; production workers do not share it. For sustained production usage, move job state to a shared store.
- The review endpoint must be protected with `NRC_REVIEW_TOKEN` and `NRC_SESSION_SECRET` in production.
