"""Non-destructive 2020 SPAR workbook ingestion. Separate from reviewed PDF parameters.

Rows are preserved with source workbook/worksheet/Excel row and validation status.
Gamma source beta is a RATE; export scale=1/rate. Normal train-UA estimates
are NOT automatically interpreted as bounded probabilities.
"""
import hashlib, json, math, re
from pathlib import Path
from database import connect

SOURCE='https://nrcoe.inl.gov/avgperf/'
SHEETS=('Component Reliability','IE Data','Train UA','Special Events')
SCHEMA='''
CREATE TABLE IF NOT EXISTS workbook_reliability_records (
 id TEXT PRIMARY KEY, workbook_sha256 TEXT NOT NULL, sheet TEXT NOT NULL,
 excel_row INTEGER NOT NULL, category TEXT NOT NULL, code TEXT NOT NULL,
 description TEXT, component TEXT, failure_mode TEXT, parameter_type TEXT,
 mean REAL, unit TEXT, distribution TEXT, alpha REAL, beta REAL,
 shape REAL, scale REAL, p05 REAL, median REAL, p95 REAL,
 exposure REAL, exposure_unit TEXT, failures REAL, component_count REAL,
 study_period TEXT, data_source TEXT, analysis TEXT, review_status TEXT NOT NULL,
 warnings_json TEXT NOT NULL, raw_json TEXT NOT NULL,
 source_url TEXT NOT NULL, imported_at TEXT DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(workbook_sha256,sheet,excel_row)
);
CREATE INDEX IF NOT EXISTS idx_workbook_records_category ON workbook_reliability_records(category,review_status);
'''

def init():
    with connect() as c:c.executescript(SCHEMA)

def number(x):
    if x is None or isinstance(x,bool):return None
    try:
        n=float(x)
        return n if math.isfinite(n) else None
    except (ValueError,TypeError):return None

def cell(row,col):return row[col-1] if col<=len(row) else None

def txt(v):return str(v).strip() if v is not None else ''

def parse(sheet,row,n):
    # Columns are from the actual ParameterEstimates2020.xlsx (2020 V1).
    if sheet=='Component Reliability':
        code=txt(cell(row,3));desc=txt(cell(row,4));dist=txt(cell(row,10))
        if not code or dist not in ('Beta','Gamma'):return None
        component=txt(cell(row,2));mode=code.split('-',1)[-1] if '-' in code else code
        basis=txt(cell(row,8)).lower();unit={'d':'per demand','h':'per hour'}.get(basis,'')
        v=dict(category='component',code=code,description=desc,component=component,failure_mode=mode,
            parameter_type='failure probability' if basis=='d' else 'failure rate',unit=unit,
            distribution=dist,p05=number(cell(row,12)),median=number(cell(row,13)),mean=number(cell(row,14)),p95=number(cell(row,15)),
            a=number(cell(row,16)),b=number(cell(row,17)),failures=number(cell(row,6)),exposure=number(cell(row,7)),
            exposure_unit='demands' if basis=='d' else 'hours' if basis=='h' else '',component_count=number(cell(row,9)),
            study_period=txt(cell(row,19)),data_source=txt(cell(row,5)),analysis=txt(cell(row,11)))
    elif sheet=='IE Data':
        code=txt(cell(row,3));desc=txt(cell(row,4));dist=txt(cell(row,8))
        if not code or dist not in ('Beta','Gamma'):return None
        v=dict(category='initiating_event',code=code,description=desc,component='Initiating event',failure_mode=code,
            parameter_type='initiating event frequency',unit='per reactor critical year',distribution=dist,
            p05=number(cell(row,10)),median=number(cell(row,11)),mean=number(cell(row,12)),p95=number(cell(row,13)),
            a=number(cell(row,14)),b=number(cell(row,15)),failures=number(cell(row,6)),exposure=number(cell(row,7)),
            exposure_unit='reactor critical years',component_count=None,study_period=txt(cell(row,17)),
            data_source=txt(cell(row,5)),analysis=txt(cell(row,9)))
    elif sheet=='Train UA':
        code=txt(cell(row,3));desc=txt(cell(row,4));dist=txt(cell(row,8))
        if not code or dist not in ('Beta','Gamma','Normal'):return None
        v=dict(category='train_unavailability',code=code,description=desc,component=txt(cell(row,2)),failure_mode=code,
            parameter_type='train unavailability',unit='probability',distribution=dist,
            p05=number(cell(row,9)),median=number(cell(row,10)),mean=number(cell(row,11)),p95=number(cell(row,12)),
            a=number(cell(row,13)),b=number(cell(row,14)),failures=None,exposure=None,exposure_unit='',
            component_count=number(cell(row,7)),study_period=txt(cell(row,17)),data_source=txt(cell(row,5)),analysis=txt(cell(row,6)))
        v['std_dev']=number(cell(row,15))
    else:
        code=txt(cell(row,1));desc=txt(cell(row,3));dist_label=txt(cell(row,14))
        if not code or not (dist_label.startswith('Beta') or dist_label.startswith('Gamma')):return None
        dist='Beta' if dist_label.startswith('Beta') else 'Gamma';basis=txt(cell(row,13)).lower()
        v=dict(category='special_event',code=code,description=desc,component=txt(cell(row,2)),failure_mode=code,
            parameter_type='special event probability' if basis=='d' else 'special event rate' if basis=='h' else 'special event parameter',
            unit='per demand' if basis=='d' else 'per hour' if basis=='h' else '',distribution=dist,
            p05=None,median=None,mean=number(cell(row,15)),p95=None,a=number(cell(row,16)),b=number(cell(row,17)),
            failures=number(cell(row,7)),exposure=number(cell(row,8)),exposure_unit='demands' if basis=='d' else 'hours' if basis=='h' else '',
            component_count=None,study_period='',data_source=txt(cell(row,12)),analysis=dist_label)
    warnings=[];a=v.pop('a');b=v.pop('b');dist=v['distribution'];mean=v['mean']
    v.update(alpha=a if dist=='Beta' else None,beta=b if dist=='Beta' else None,
             shape=a if dist=='Gamma' else None,scale=1/b if dist=='Gamma' and b and b>0 else None)
    if mean is None or mean<0:warnings.append('Missing or invalid mean')
    if not v['unit']:warnings.append('Unresolved denominator/units')
    if dist in ('Beta','Gamma'):
        if a is None or b is None or a<=0 or b<=0:warnings.append('Missing/invalid distribution parameters')
        elif mean is not None:
            expected=a/(a+b) if dist=='Beta' else a/b
            if abs(expected-mean)>max(abs(mean)*0.04,1e-15):warnings.append('Distribution mean differs by >4%')
    if v['unit'] in ('per demand','probability') and mean is not None and mean>1:warnings.append('Probability exceeds 1')
    if all(v[k] is not None for k in ('p05','median','p95')):
        if not v['p05']<=v['median']<=v['p95']:warnings.append('Quantiles not ordered')
    if sheet=='Train UA' and dist=='Normal':
        warnings.append('Normal unavailability is unbounded; confirm truncation/physical limits before simulation')
        if v.get('p05') is not None and v['p05']<0:warnings.append('Negative 5th percentile in source')
    if sheet=='Special Events':warnings.append('Special event definition and applicability require source review')
    v['warnings_json']=json.dumps(warnings)
    v['review_status']='needs_review' if warnings else 'validated'
    v['raw_json']=json.dumps({'row':n,'cells':[str(x) if x is not None else None for x in row[:22]],'std_dev':v.pop('std_dev',None)},ensure_ascii=False)
    return v

def ingest_workbook(path,source_url=SOURCE):
    from openpyxl import load_workbook
    path=Path(path);raw=path.read_bytes()
    if not raw.startswith(b'PK'):raise ValueError('Expected .xlsx workbook')
    digest=hashlib.sha256(raw).hexdigest()
    wb=load_workbook(path,read_only=True,data_only=True)
    if any(s not in wb.sheetnames for s in SHEETS):raise ValueError('Unsupported workbook layout: expected all four 2020 SPAR worksheets')
    init();totals={s:{'validated':0,'needs_review':0} for s in SHEETS}
    cols=['id','workbook_sha256','sheet','excel_row','category','code','description','component','failure_mode','parameter_type',
          'mean','unit','distribution','alpha','beta','shape','scale','p05','median','p95','exposure','exposure_unit',
          'failures','component_count','study_period','data_source','analysis','review_status','warnings_json','raw_json','source_url']
    with connect() as c:
        for sheet in SHEETS:
            previous_component=''
            for n,row in enumerate(wb[sheet].iter_rows(min_row=6,values_only=True),6):
                item=parse(sheet,row,n)
                if item and sheet=='Component Reliability':
                    if item['component']:previous_component=item['component']
                    else:item['component']=previous_component
                    if not item['component']:
                        issues=json.loads(item['warnings_json']);issues.append('Missing component heading')
                        item['warnings_json']=json.dumps(issues);item['review_status']='needs_review'
                if not item:continue
                item.update(id=hashlib.sha256(f'{digest}:{sheet}:{n}'.encode()).hexdigest()[:32],workbook_sha256=digest,
                            sheet=sheet,excel_row=n,source_url=source_url)
                # Do not change decisions from earlier imports; only insert new records.
                c.execute(f'INSERT OR IGNORE INTO workbook_reliability_records ({",".join(cols)}) VALUES ({",".join("?" for _ in cols)})',
                          [item.get(k) for k in cols])
                totals[sheet][item['review_status']]+=1
    wb.close()
    return {'workbook_sha256':digest,'sheets':totals,'note':'Existing PDF reliability parameters and previous reviewer decisions were not modified.'}

def get_records(status=None,category=None,limit=1000):
    init();where=[];params=[]
    if status:where.append('review_status=?');params.append(status)
    if category:where.append('category=?');params.append(category)
    sql='SELECT * FROM workbook_reliability_records'+(' WHERE '+' AND '.join(where) if where else '')+' ORDER BY sheet,excel_row LIMIT ?'
    with connect() as c:return [dict(r) for r in c.execute(sql,params+[min(max(int(limit),1),10000)])]

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('workbook');args=p.parse_args()
    print(json.dumps(ingest_workbook(args.workbook),indent=2))
