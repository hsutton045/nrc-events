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
