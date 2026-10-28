"""Offline extraction/integration smoke test; set NRC_DB_PATH to a disposable file."""
import os, sys
from pathlib import Path
if not os.getenv('NRC_DB_PATH'):raise SystemExit('Set NRC_DB_PATH to a disposable test DB first')
from bulk_reliability_import import ingest_bulk
from database import connect
p=Path(sys.argv[1] if len(sys.argv)>1 else str(Path.home()/'Downloads'/'AvgPara2020.pdf'))
a=ingest_bulk(p);b=ingest_bulk(p)
with connect() as c:
    count=c.execute('SELECT COUNT(*) FROM reliability_parameters').fetchone()[0]
    edg={r['failure_mode']:dict(r) for r in c.execute("SELECT * FROM reliability_parameters WHERE component='Emergency Diesel Generator'")}
assert count==a['automatically_validated']==b['automatically_validated'],(count,a,b)
assert edg['FTS']['unit']=='per demand'
assert edg['FTLR']['unit']=='per hour'
assert edg['FTR']['unit']=='per hour'
print('PASS: bulk extraction, duplicate prevention, EDG units; verified records:',count)
