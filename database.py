from __future__ import annotations
import json, os, re, sqlite3
from datetime import datetime
from pathlib import Path

DB_PATH = Path(os.getenv('NRC_DB_PATH', 'data/nrc_events.db'))
FOOTER_MARKERS = ('Return to top\n', '\nHome\n\nNews Releases\n', '\nFooter Bottom\n')

def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con=sqlite3.connect(DB_PATH); con.row_factory=sqlite3.Row
    con.execute('PRAGMA journal_mode=WAL'); con.execute('PRAGMA foreign_keys=ON')
    return con

def clean_event_text(text:str)->str:
    if not text: return ''
    text=text.replace('\xa0',' ').replace('\r\n','\n').replace('\r','\n')
    text=re.split(r'\n\s*Return to top\s*(?:\n|$)', text, maxsplit=1, flags=re.I)[0]
    for marker in FOOTER_MARKERS:
        i=text.find(marker)
        if i>=0: text=text[:i]
    text=re.sub(r'[ \t]+',' ',text); text=re.sub(r'\n{3,}','\n\n',text)
    return text.strip()

def init_db():
    with connect() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS events(
          event_number TEXT PRIMARY KEY, report_date TEXT, report_date_iso TEXT,
          facility TEXT, state TEXT, title TEXT, event_text TEXT NOT NULL,
          report_url TEXT, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE INDEX IF NOT EXISTS idx_events_date ON events(report_date_iso);
        CREATE INDEX IF NOT EXISTS idx_events_facility ON events(facility);
        CREATE TABLE IF NOT EXISTS embeddings(
          event_number TEXT PRIMARY KEY REFERENCES events(event_number) ON DELETE CASCADE,
          model TEXT NOT NULL, dimensions INTEGER NOT NULL, vector BLOB NOT NULL,
          content_hash TEXT NOT NULL, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS ai_classifications(
          cache_key TEXT NOT NULL, event_number TEXT NOT NULL REFERENCES events(event_number) ON DELETE CASCADE,
          model TEXT NOT NULL, relevant INTEGER NOT NULL, confidence REAL,
          component TEXT, subcomponent TEXT, failure_mode TEXT, failure_cause TEXT,
          failure_mechanism TEXT, initiating_event TEXT, plant_response TEXT,
          automatic_actions TEXT, operator_actions TEXT, redundant_system_response TEXT,
          reactor_trip INTEGER, power_reduction INTEGER, safety_system_actuation INTEGER,
          restoration_action TEXT, common_cause_indicator INTEGER, human_error_indicator INTEGER,
          maintenance_related INTEGER, event_outcome TEXT, evidence TEXT, rationale TEXT,
          raw_json TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY(cache_key,event_number));
        CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT);
        ''')

def parse_date(s):
    for f in ('%B %d, %Y','%b %d, %Y','%m/%d/%Y','%m-%d-%Y','%Y-%m-%d'):
        try:return datetime.strptime((s or '').strip(),f).date().isoformat()
        except ValueError: pass
    return None

def upsert_events(events):
    init_db(); n=0
    with connect() as c:
        for e in events:
            num=str(e.get('event_number','')).strip()
            if not num: continue
            txt=clean_event_text(str(e.get('event_text','')))
            title=str(e.get('title','')).strip() or (next((x for x in txt.splitlines() if x.strip()),'Event'))
            c.execute('''INSERT INTO events(event_number,report_date,report_date_iso,facility,state,title,event_text,report_url,updated_at)
            VALUES(?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP) ON CONFLICT(event_number) DO UPDATE SET
            report_date=excluded.report_date,report_date_iso=excluded.report_date_iso,facility=excluded.facility,
            state=excluded.state,title=excluded.title,event_text=excluded.event_text,report_url=excluded.report_url,updated_at=CURRENT_TIMESTAMP''',
            (num,e.get('report_date',''),parse_date(e.get('report_date','')),e.get('facility',''),e.get('state',''),title,txt,e.get('report_url',''))); n+=1
    return n

def rowdict(r): return dict(r) if r else None

def get_event(num):
    with connect() as c:return rowdict(c.execute('SELECT * FROM events WHERE event_number=?',(str(num),)).fetchone())

def search_keyword(keywords='',exclude='',start=None,end=None,limit=500):
    terms=[x.lower() for x in keywords.split() if x]; ex=[x.lower() for x in exclude.split() if x]
    q='SELECT * FROM events WHERE 1=1'; p=[]
    if start:q+=' AND report_date_iso>=?';p.append(start)
    if end:q+=' AND report_date_iso<=?';p.append(end)
    q+=' ORDER BY report_date_iso DESC LIMIT ?';p.append(limit*10 if terms or ex else limit)
    with connect() as c: rows=[dict(x) for x in c.execute(q,p)]
    def text(e):return ' '.join(str(e.get(k,'')) for k in ('event_number','facility','state','title','event_text')).lower()
    return [e for e in rows if all(t in text(e) for t in terms) and not any(t in text(e) for t in ex)][:limit]

def all_events_for_embedding():
    with connect() as c:return [dict(x) for x in c.execute('SELECT * FROM events ORDER BY event_number')]

def set_meta(k,v):
    with connect() as c:c.execute('INSERT INTO metadata(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(k,str(v)))
def get_meta(k,default=''):
    with connect() as c:
        r=c.execute('SELECT value FROM metadata WHERE key=?',(k,)).fetchone(); return r['value'] if r else default

def database_last_updated():
    """Return the recorded scraper update time, or fall back to the latest DB row update."""
    recorded=get_meta('last_updated','').strip()
    if recorded:
        return recorded
    with connect() as c:
        r=c.execute('SELECT MAX(updated_at) AS updated_at FROM events').fetchone()
        return (r['updated_at'] if r and r['updated_at'] else 'Unknown')

def import_json(path):
    with open(path,encoding='utf-8') as f:data=json.load(f)
    return upsert_events(data)
