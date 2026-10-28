"""Versioned, reviewable reliability study and parameter ingestion. No fabricated rates."""
import csv, json, math, sqlite3, hashlib, os
from pathlib import Path
from database import connect

SCHEMA='''
CREATE TABLE IF NOT EXISTS reliability_studies(id TEXT PRIMARY KEY,title TEXT NOT NULL,source_url TEXT NOT NULL,publisher TEXT,study_period TEXT,population TEXT,imported_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS reliability_parameters(id TEXT PRIMARY KEY,study_id TEXT NOT NULL REFERENCES reliability_studies(id),component TEXT NOT NULL,failure_mode TEXT NOT NULL,parameter_type TEXT NOT NULL,value REAL NOT NULL,unit TEXT NOT NULL,distribution TEXT,alpha REAL,beta REAL,shape REAL,scale REAL,lower_bound REAL,upper_bound REAL,exposure REAL,exposure_unit TEXT,notes TEXT,review_status TEXT NOT NULL DEFAULT 'unreviewed',imported_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS idx_reliability_component ON reliability_parameters(component,failure_mode);
'''
FIELDS=['study_id','title','source_url','publisher','study_period','population','component','failure_mode','parameter_type','value','unit','distribution','alpha','beta','shape','scale','lower_bound','upper_bound','exposure','exposure_unit','notes','review_status']
NUMERIC=['value','alpha','beta','shape','scale','lower_bound','upper_bound','exposure']

def init():
    with connect() as c:c.executescript(SCHEMA)

def ingest_csv(path):
    init(); accepted=[]; errors=[]
    with open(path,encoding='utf-8-sig',newline='') as f:
        reader=csv.DictReader(f)
        missing=set(['study_id','title','source_url','component','failure_mode','parameter_type','value','unit'])-set(reader.fieldnames or [])
        if missing:raise ValueError('Missing columns: '+', '.join(sorted(missing)))
        for line,r in enumerate(reader,2):
            try:
                d={k:(r.get(k) or '').strip() for k in FIELDS}
                if not d['source_url'].startswith('https://'):raise ValueError('source_url must be HTTPS')
                if any(not d[k] for k in ['study_id','title','component','failure_mode','parameter_type','unit']):raise ValueError('missing required field')
                for k in NUMERIC:d[k]=float(d[k]) if d[k] else None
                if d['value'] is None or not math.isfinite(d['value']) or d['value']<0:raise ValueError('invalid value')
                for k in NUMERIC:
                    if d[k] is not None and (not math.isfinite(d[k]) or d[k]<0):raise ValueError('invalid '+k)
                if d['parameter_type'].lower().find('probability')>=0 and d['value']>1:raise ValueError('probability > 1')
                if d['distribution'].lower()=='beta' and (not d['alpha'] or not d['beta']):raise ValueError('Beta requires alpha and beta')
                if d['distribution'].lower()=='gamma' and (not d['shape'] or not d['scale']):raise ValueError('Gamma requires shape and scale')
                if d['lower_bound'] is not None and d['upper_bound'] is not None and d['lower_bound']>d['upper_bound']:raise ValueError('reversed bounds')
                if d['review_status'] not in ('','unreviewed','verified'):raise ValueError('review_status must be unreviewed or verified')
                d['review_status']=d['review_status'] or 'unreviewed'
                d['id']=hashlib.sha256(json.dumps([d['study_id'],d['component'],d['failure_mode'],d['parameter_type'],d['unit']],sort_keys=True).encode()).hexdigest()[:24]
                accepted.append(d)
            except (ValueError,TypeError) as e:errors.append({'line':line,'error':str(e)})
    with connect() as c:
        for d in accepted:
            c.execute('INSERT INTO reliability_studies(id,title,source_url,publisher,study_period,population) VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET title=excluded.title,source_url=excluded.source_url,publisher=excluded.publisher,study_period=excluded.study_period,population=excluded.population',tuple(d[k] for k in ['study_id','title','source_url','publisher','study_period','population']))
            cols=['id','study_id','component','failure_mode','parameter_type','value','unit','distribution','alpha','beta','shape','scale','lower_bound','upper_bound','exposure','exposure_unit','notes','review_status']
            c.execute(f'INSERT INTO reliability_parameters ({",".join(cols)}) VALUES ({",".join("?" for _ in cols)}) ON CONFLICT(id) DO UPDATE SET '+','.join(f'{k}=excluded.{k}' for k in cols if k!='id'),tuple(d[k] for k in cols))
    return {'imported':len(accepted),'rejected':len(errors),'errors':errors}

def search(query,limit=30):
    init(); terms=[x for x in __import__('re').findall(r'[a-z0-9]+',query.lower()) if len(x)>2 and x not in {'find','show','failures','failure','rates','rate','the','and','for','with','component'}]
    with connect() as c:
        rows=[dict(r) for r in c.execute('''SELECT p.*,s.title,s.source_url,s.publisher,s.study_period,s.population FROM reliability_parameters p JOIN reliability_studies s ON s.id=p.study_id WHERE p.review_status='verified' ''')]
    scored=[]
    for row in rows:
        hay=' '.join(str(row.get(k) or '') for k in ['component','failure_mode','title','notes']).lower()
        score=sum(3 if t in (row['component']+' '+row['failure_mode']).lower() else 1 for t in terms if t in hay)
        if score:scored.append((score,row))
    return [r for _,r in sorted(scored,key=lambda v:(-v[0],v[1]['component']))[:limit]]

def export_rows(rows):
    return [{k:r.get(k) for k in ['id','study_id','title','source_url','publisher','study_period','population','component','failure_mode','parameter_type','value','unit','distribution','alpha','beta','shape','scale','lower_bound','upper_bound','exposure','exposure_unit','notes','review_status']} for r in rows]

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('csv_file');a=p.parse_args();print(json.dumps(ingest_csv(a.csv_file),indent=2))
