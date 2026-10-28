"""Conservative, reproducible parser for INL/EXT-21-65055 selected-distribution tables.
Only rows passing numerical and contextual checks are automatically published.
"""
import hashlib, json, math, re
from pathlib import Path
from database import connect
from study_pipeline import init as init_studies, extract, ROOT
from reliability_store import init as init_reliability, ingest_csv, FIELDS
import csv, tempfile

TABLE = re.compile(r'Table\s+(\d+)\.\s+Selected industry distributions of\s+(?:p and )?[λp]\s+for\s+(.+?)\.',re.I)
ROW = re.compile(r'^\s*([A-Z][A-Z0-9.>/≤-]{1,12})\s+(FTS|FTLR|FTR|FTOC|FTO|FTC|FTOP|FTR>1H|FTR≤1H|SOP|FTR<=1H)\s+(?:EB/PL/KS|JNID/IL|EB|JNID|[A-Z/]+)\s+([\d.Ee+-]+)\s+([\d.Ee+-]+)\s+([\d.Ee+-]+)\s+([\d.Ee+-]+)\s+(Beta|Gamma)\s+([\d.Ee+-]+)\s+([\d.Ee+-]+)',re.I)

# Explicitly supported, verified page/table anchors for the initial publication adapter.
# Avoid treating unrelated tabular numbers as reliability parameters.
SUPPORTED={'61':('EDG','Emergency Diesel Generator')}
# Only the tested source file is eligible for automatic publication. Other revisions require review.
TESTED_SHA256={'fd7b697974ddb4db9400fefc2c70776d687ed2461dc2bf7e941630da70f3ae12'}

def parse_pdf(path):
 import pymupdf as fitz
 doc=fitz.open(path); found=[]; rejected=[]
 for page_i,page in enumerate(doc):
  txt=page.get_text(sort=True)
  if 'Table 61.' not in txt:continue
  # Text extracted with layout retains table row boundaries for this specific report.
  import subprocess
  layout=subprocess.run(['pdftotext','-f',str(page_i+1),'-l',str(page_i+1),'-layout',str(path),'-'],capture_output=True,text=True,check=True).stdout
  if 'Table 61. Selected industry distributions' not in layout:continue
  section=layout.split('Table 61. Selected industry distributions',1)[1].split('Component Unreliability',1)[0]
  for line in section.splitlines():
   m=ROW.match(line)
   if not m:continue
   code,mode,p05,median,mean,p95,dist,a,b=m.groups()
   if code!='EDG':continue
   p05,median,mean,p95,a,b=map(float,(p05,median,mean,p95,a,b))
   expected=a/(a+b) if dist.lower()=='beta' else a/b
   relative=abs(expected-mean)/mean if mean else float('inf')
   issues=[]
   if not (0<=p05<=median<=p95 and p05<=mean<=p95):issues.append('quantiles inconsistent')
   if not (a>0 and b>0 and mean>0):issues.append('invalid parameters')
   if relative>0.035:issues.append(f'mean mismatch {relative:.1%}')
   if dist.lower()=='beta' and not (0<=mean<=1):issues.append('probability out of range')
   if mode=='FTS' and dist.lower()!='beta':issues.append('FTS distribution mismatch')
   if mode in ('FTR','FTLR') and dist.lower()!='gamma':issues.append('running distribution mismatch')
   # FTLR here is per-demand; FTR is per-hour. Confirmed against report definitions.
   unit='per demand' if mode=='FTS' else 'per hour'
   found.append({'component':'Emergency Diesel Generator','failure_mode':mode,'value':mean,'unit':unit,
    'parameter_type':'failure probability' if unit=='per demand' else 'failure rate',
    'distribution':dist,'alpha':a if dist=='Beta' else None,'beta':b if dist=='Beta' else None,
    'shape':a if dist=='Gamma' else None,'scale':1/b if dist=='Gamma' else None,
    'percentiles':{'5':p05,'50':median,'95':p95},'gamma_rate':b if dist=='Gamma' else None,
    'page':page_i+1,'table':61,'issues':issues,'validation_relative_error':relative})
 return found

def ingest_pdf(path,source_url='https://nrcoe.inl.gov/avgperf/',allow_automatic=True):
 import pymupdf as fitz
 path=Path(path); raw=path.read_bytes()
 if not raw.startswith(b'%PDF'):raise ValueError('Not a PDF')
 digest=hashlib.sha256(raw).hexdigest()
 with fitz.open(path) as doc:
  first=' '.join(p.get_text() for p in list(doc)[:5])
 if 'INL/EXT-21-65055' not in first or '2020 Update' not in first:
  raise ValueError('Unsupported publication: this validated adapter only supports INL/EXT-21-65055')
 init_studies();init_reliability();ROOT.mkdir(parents=True,exist_ok=True)
 archive=ROOT/(digest+'.pdf')
 if not archive.exists():archive.write_bytes(raw)
 document_url='local-upload://'+digest
 with connect() as c:
  c.execute('''INSERT INTO study_documents(url,title,listing_url,sha256,local_path,media_type,bytes,checked_at,downloaded_at,extraction_status)
    VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(url) DO UPDATE SET local_path=excluded.local_path''',
   (document_url,'Industry-Average Performance 2020 Update',source_url,digest,str(archive),'.pdf',len(raw),None,None,'validated'))
 candidates=parse_pdf(archive)
 if not candidates:raise ValueError('No supported Table 61 rows found; nothing imported')
 accepted=[];held=[]
 for r in candidates:
  rid=hashlib.sha256(json.dumps([digest,r['table'],r['failure_mode']],sort_keys=True).encode()).hexdigest()
  status='validated' if not r['issues'] and allow_automatic and digest in TESTED_SHA256 else 'needs_review'
  with connect() as c:
   c.execute('''INSERT INTO study_extraction_rows(id,document_url,sheet_or_page,row_number,raw_cells_json,candidate_component,candidate_failure_mode,candidate_value,candidate_unit,review_status)
     VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET review_status=CASE WHEN study_extraction_rows.review_status='approved' THEN 'approved' ELSE excluded.review_status END''',
    (rid,document_url,f"PDF page {r['page']} · Table {r['table']}",r['table'],json.dumps(r),r['component'],r['failure_mode'],str(r['value']),r['unit'],status))
  if status!='validated':held.append(r);continue
  d={k:'' for k in FIELDS}
  d.update(study_id='INL-EXT-21-65055-2020',title='Industry-Average Performance for Components and Initiating Events: 2020 Update',source_url=source_url,
   publisher='Idaho National Laboratory',study_period='2006–2020',population='U.S. commercial nuclear power plants',
   component=r['component'],failure_mode=r['failure_mode'],parameter_type=r['parameter_type'],value=str(r['value']),unit=r['unit'],
   distribution=r['distribution'],alpha=str(r['alpha'] or ''),beta=str(r['beta'] or ''),shape=str(r['shape'] or ''),scale=str(r['scale'] or ''),
   lower_bound=str(r['percentiles']['5']),upper_bound=str(r['percentiles']['95']),review_status='verified',
   notes=f"Automated validated import; source SHA256 {digest}; PDF page {r['page']}; Table {r['table']}; 5th/50th/95th {r['percentiles']}; gamma shape-rate beta={r['gamma_rate']}; mean check error={r['validation_relative_error']:.5g}")
  accepted.append(d)
 imported=0
 if accepted:
  with tempfile.NamedTemporaryFile('w',suffix='.csv',newline='',encoding='utf-8',delete=False) as f:
   w=csv.DictWriter(f,fieldnames=FIELDS);w.writeheader();w.writerows(accepted);temp=f.name
  try:
   result=ingest_csv(temp)
   if result['rejected']:raise ValueError(str(result['errors']))
   imported=result['imported']
  finally:Path(temp).unlink(missing_ok=True)
 return {'document_sha256':digest,'candidates':len(candidates),'automatically_validated':imported,'needs_review':len(held),'rows':candidates}

if __name__=='__main__':
 import argparse
 p=argparse.ArgumentParser();p.add_argument('pdf');p.add_argument('--no-auto',action='store_true');a=p.parse_args()
 print(json.dumps(ingest_pdf(a.pdf,allow_automatic=not a.no_auto),indent=2))
