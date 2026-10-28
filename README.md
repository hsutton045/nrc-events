# NRC Event Research v2

## Environment
Set `OPENAI_API_KEY` for AI features. Optional: `NRC_DB_PATH`, `OPENAI_EMBED_MODEL`, `OPENAI_EMBED_DIM`, `OPENAI_AI_MODEL`.

## First migration from the existing JSON
```bash
pip install -r requirements.txt
python migrate_json.py events.json
python embed_events.py
```

## Database updates
Incremental update (starts at latest saved report date):
```bash
python build_events.py
python embed_events.py
```
Full rebuild from NRC archives:
```bash
python build_events.py --full --start-year 1999 --end-year 2026
python embed_events.py
```
`embed_events.py` only embeds new/changed records unless `--force` is supplied.

## Run locally
```bash
gunicorn -w 2 -b 127.0.0.1:5000 app:app --timeout 180
```

## Render
Configure `OPENAI_API_KEY` as a secret environment variable. For runtime database updates that survive deploys/restarts, attach persistent storage and set `NRC_DB_PATH` to a path on that disk (for example `/var/data/nrc_events.db`, depending on your Render disk mount). Run migration/embedding once against that persistent database.

The AI workflow is: semantic retrieval -> conservative relevance classification -> structured extraction -> deterministic Python statistics -> optional narrative research report. Statistics are explicitly about the retrieved NRC event set, not component failure probabilities/rates.


## Reliability reference integration
The UI links to NRC/INL operational-experience, industry-average parameter, and emergency power studies. It does not download or invent numerical estimates.

For verified numerical parameters, populate `reliability_parameters_template.csv` with values transcribed from a published study, including failure mode, denominator units (per demand versus per hour), study population and period, source URL, and uncertainty fields. Save it on the Render persistent disk (for example `/opt/render/project/src/data/reliability_parameters.csv`) and set `NRC_RELIABILITY_CSV` to that absolute path. The web UI shows potentially related parameters with a verification warning; the concise report can reference them separately from event counts. No automatic rates are calculated from these parameters.

Keep the existing SQLite database; this release adds no schema migration. The example CSV is deliberately empty to avoid fabricated scientific estimates.

## Reliability database (new)
`reliability_store.py` creates SQLite tables `reliability_studies` and `reliability_parameters` in the SAME persistent database as events. It ingests **reviewed/curated CSV extractions**, with required source URLs, mode, units, study identifiers and optional distribution parameters. This is a conservative, auditable ingestion pipeline, **not an automatic NRC PDF table scraper**. No reliability estimates are prepopulated; do not claim the system already contains published rates.

1. Locate authoritative studies at https://nrcoe.inl.gov/avgperf/ and https://nrcoe.inl.gov/SysStudy/EPS .
2. Extract and verify each parameter, unit, failure-mode definition, population, observation period and uncertainty distribution against the original publication. Enter in `reliability_import_template.csv` (one row per parameter). Set `review_status=verified` only after human verification.
3. Set `NRC_DB_PATH` to your existing persistent SQLite database, then run `python reliability_store.py your_verified_parameters.csv` in Render Shell. This upserts by stable study/component/mode/type/unit key, and rejects malformed rows.
4. AI Search displays matched reliability parameters alongside classified events; research analysis receives both. Export matching parameters using JSON (including distribution parameters) or CSV. **These exports contain parameters, not sampled Monte Carlo trajectories.**

No existing events, embeddings or classifications are deleted. Search uses lexical matching of component/mode/study titles for reliability parameters; synonym matching and independent study full-text indexing remain future work. No automatic PDF download, extraction, validation, or scheduled synchronization is implemented. Test in a staging deployment before production. Existing `NRC_RELIABILITY_CSV` is no longer read; import CSV with the command above.

## NRC/INL study collection and review queue (v3)

This version discovers linked PDF, XLSX, XLS, and CSV documents from the NRC/INL operational-experience pages, archives content-addressed source files, and stages spreadsheet cells / extractable PDF tables for review in SQLite. It does **not** automatically approve extracted rates: ambiguous headers, denominators, distributions, population and PRA applicability require source verification.

```bash
pip install -r requirements.txt
python study_pipeline.py sync --max-downloads 20
python study_pipeline.py export-staging review_queue.csv
```

The review queue is an audit aid, **not** an import file. After verification, transfer approved values into `reliability_import_template.csv` and import using `python reliability_store.py verified.csv`. The AI search only uses approved/imported `reliability_parameters`, never raw staged tables. Re-running sync detects unchanged documents by SHA-256 and preserves the downloaded archive. Study files default to a `studies/` folder next to `NRC_DB_PATH`; override using `NRC_STUDY_ARCHIVE`. Ensure that folder is on the persistent disk in Render. Source site layout and PDFs can change; inspect errors and validate extracted tables manually. The pipeline has not been live-tested against the source website in this environment due to network restrictions.

Do not run the synchronization in a web request. Run from the Render Shell or a controlled worker, with adequate disk capacity and rate limits. No scheduled synchronization is configured.

## Human review interface (v4)

Set `NRC_REVIEW_TOKEN` to a long random secret and `NRC_SESSION_SECRET` to a separate long random secret on Render. Access `/review/login` and sign in with the review token. `/review` lists extracted table rows, shows source-document links and raw cells, and lets the reviewer approve a *manually transcribed and verified* reliability parameter or reject a row. Approved parameters are validated and imported into `reliability_parameters` and appear in unified research search/exports. Review decisions persist across repeated extraction of unchanged rows.

**Security:** Do not share the token. Use HTTPS. The review endpoint is protected by session authentication and CSRF checks. Use a single stable `NRC_SESSION_SECRET` across web workers. Set `NRC_REVIEW_TOKEN` before deployment; if absent, review access is denied. The app does not automatically approve PDF-extracted values. Reviewers must verify units, parameter definitions, population, and statistical uncertainty against the original source. The existing search may display unreviewed imported parameters; filter on `review_status=verified` before treating any parameter as a simulation input.

## V5: tested publication adapter, automatic checks, and PDF upload

- A dedicated **INL/EXT-21-65055 (2020 Update)** adapter parses **Table 61 (EDG)** from the PDF, validates its Beta/Gamma distribution means against published means, verifies quantile order and failure-mode/unit rules, and stores original file SHA-256 plus exact PDF page/table provenance.
- **Automatic publication is allowlisted to the tested source PDF SHA-256**, not all documents claiming the same report ID. New versions are staged for review. The adapter intentionally supports **Table 61 only**; other tables are NOT silently imported. Expand via separately tested adapters.
- Import through authenticated `/review` PDF upload, or run `python validated_import.py /path/to/AvgPara2020.pdf`.
- Only `review_status='verified'` parameters are searchable/exportable. Unreviewed rows do not appear in quantitative search.
- The generic NRC/INL web scraper still receives Cloudflare challenges; browser download and PDF upload are the supported path for this release.
- Gamma source uses **shape/rate** (alpha/beta); the database stores **shape/scale**, converting scale=1/beta. The original beta rate is retained in provenance notes.
- PDF extraction requires the `pdftotext` command (poppler-utils) and PyMuPDF (`pymupdf`).

### Fedora local smoke test (Fish shell)

```fish
source .venv/bin/activate.fish
pip install -r requirements.txt
sudo dnf install poppler-utils
set -x NRC_DB_PATH "$PWD/data/test_reliability.sqlite"
python validated_import.py /path/to/AvgPara2020.pdf
python validated_import.py /path/to/AvgPara2020.pdf  # repeat: should not duplicate parameters
python -c 'from reliability_store import search; print([(r["failure_mode"],r["value"],r["unit"],r["distribution"]) for r in search("Emergency Diesel Generator")])'
```

Start Flask with your existing `NRC_REVIEW_TOKEN` and `NRC_SESSION_SECRET`, sign into `/review/login`, and upload the same PDF. Check the validated tab and unified AI Search. The PDF import is designed to be idempotent. For production, back up the SQLite file first.

## v6: bulk reliability extraction and standalone library

**New:** `bulk_reliability_import.py` scans selected-distribution tables in Appendix A of the
byte-identical 2020 INL report, validates reported means against Beta/Gamma parameters,
and records source PDF page, table, pooling group, quantiles, and source SHA256. Rows with
unsupported modes or failed checks are retained in the review queue, not published.
The standalone `/reliability` page supports component, mode and text filtering without
an event database, embeddings, or OpenAI API key. `/api/reliability/browse` provides JSON.
The `/review` PDF upload now runs the bulk importer.

**Corrected:** EDG FTLR is a per-hour rate (Table 60 reports 53,343 operating hours),
not a per-demand probability. Importing the same PDF with v6 updates the existing
FTLR record in-place, including its unit and parameter type. FTS remains per demand.

**Run (Fish shell):**

```fish
source .venv/bin/activate.fish
pip install -r requirements.txt
sudo dnf install poppler-utils
set -x NRC_DB_PATH "$PWD/data/reliability_test.db"
python bulk_reliability_import.py ~/Downloads/AvgPara2020.pdf
python app.py
```

Open `/reliability` to browse the imported records and `/review` to inspect held rows.
You can import twice to verify that the database count stays unchanged.
To update an existing v5 database, **do not change `NRC_DB_PATH`**; run the v6 bulk
import on that database. Back it up first. The importer uses the existing study ID and
upserts matching parameters; it does not touch NRC events or embeddings.

**Limitations:** This is a conservative Appendix A adapter, not universal PDF table
recognition. It extracts rows whose text layout matches the supported format and
may omit additional tables or component categories. Some system/pooling-group labels
are abbreviations from the report and should be checked against its glossary. The
source study is industry-averaged, and applicability to a specific plant or failure
model must be independently evaluated. Mathematical checks do not prove that an
extracted unit or component interpretation is correct. In particular, uncommon
failure modes are held for review. The original AI Search remains unchanged; the
standalone library is independent, while the main search continues to show matching
published parameters alongside event results when available.

## v7 — Component-level matching and AnyLogic model inputs

This release adds `anylogic_export.py`, a conservative component matcher and simulation-oriented export.

- `/api/reliability/match?component=edg` — verified, exact-category/explicit-alias component matches; optional `&mode=FTS`.
- `/api/reliability/anylogic?component=edg&format=json` — provenance-rich simulation parameter JSON.
- `/api/reliability/anylogic?component=edg&format=csv` — flattened simulation parameter CSV.
- `/reliability` — dedicated download buttons that respect **component and failure-mode filters** (not free-text browsing).
- `python test_v7.py` — isolated SQLite, matching, and Flask route checks. Requires project dependencies.

### Important modeling limitations

A per-demand probability is sampled as a probability and evaluated on a demand; a per-hour rate is sampled as a rate, not a waiting time. If a constant-hazard approximation is justified, an hourly rate may parameterize an exponential waiting time **after** sampling the rate. Gamma/Beta distributions in this report describe uncertainty in a parameter, not directly a component's repair duration. Repair-time data are intentionally null. Event counts are never converted to failure rates without exposures.

Matching is intentionally conservative: `rcp`/`reactor coolant pump` will not silently map to generic pump categories. If no exact category exists, use the library to review published categories and select one explicitly, documenting applicability. Existing v6 import/review and event search remain unchanged.

### Upgrade without overwriting your data

1. Back up your local SQLite database using SQLite's backup API or the `.backup` command.
2. Copy the **contents** of `nrc_v7/` over your application source files. Preserve `data/`, `.venv/`, `.env`, and Render disk data.
3. In Fish: `source .venv/bin/activate.fish` and `pip install -r requirements.txt`.
4. Set `NRC_DB_PATH` to your **local test database** if needed; do not point tests at production.
5. Run `python test_v7.py`, then `python app.py` and visit `/reliability`.
6. Choose `Emergency Diesel Generator` and optionally `FTS`, download JSON and CSV, verify units, shape/scale and source fields.
7. Visit `/api/reliability/match?component=rcp` and verify **zero matches** unless a genuine matching source category is present.

No existing event data or embeddings need to be downloaded or rebuilt to test the v7 reliability features.
