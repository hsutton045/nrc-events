"""Conservative bulk extraction of Appendix A selected distributions in INL/EXT-21-65055.
Only source-verified, mathematically coherent rows are published; uncertain rows are queued.
Requires pdftotext (poppler-utils). Does not infer demand/hour units from distribution family.
"""
import hashlib, json, re, subprocess, tempfile, csv
from pathlib import Path
from database import connect
from reliability_store import FIELDS, init as init_reliability, ingest_csv
from study_pipeline import init as init_studies, ROOT
from validated_import import TESTED_SHA256

HEADING=re.compile(r'^\s*Table\s+(\d+)\.\s+Selected industry distributions of\s+(.+?)\s+for\s+(.+?)\.\s*$',re.I)
ROW=re.compile(r'^\s*([A-Z][A-Z0-9./-]{1,14})\s+([A-Z][A-Z0-9><=≤-]{1,15})\s+([A-Z][A-Z/]+)\s+([\d.Ee+-]+)\s+([\d.Ee+-]+)\s+([\d.Ee+-]+)\s+([\d.Ee+-]+)\s+(Beta|Gamma)\s+([\d.Ee+-]+)\s+([\d.Ee+-]+)\s*$')
# FTS is per demand; FTLR and FTR are operating-hour failures (see EDG Table 60).
DEMAND={'FTS','FTO','FTOC','FTC','FTOP','SOP'}
HOURS={'FTLR','FTR','FTR>1H','FTR≤1H','FTR<=1H'}
ALIASES={'EDG':'Emergency Diesel Generator','MDP':'Motor Driven Pump','MOV':'Motor Operated Valve','AOV':'Air Operated Valve','CRB':'Circuit Breaker','XFR':'Transformer','MCC':'Motor Control Center','DCP':'DC Power Supply','BAT':'Battery','INV':'Inverter','RLY':'Relay','FAN':'Fan','CMP':'Compressor','HTX':'Heat Exchanger','SEN':'Sensor'}

def extract_rows(pdf):
    pages=subprocess.check_output(['pdftotext','-layout',str(pdf),'-'],text=True).split('\f')
    rows=[]; seen=set()
    for page_no,page in enumerate(pages,1):
        lines=page.splitlines(); active=None
        for line in lines:
            h=HEADING.match(line)
            if h:
                num,quantity,component=h.groups()
                # Appendix A selected component distributions only. Do not ingest initiating events.
                active=(int(num),quantity,component) if page_no>=40 and page_no<=185 and ('p' in quantity.lower() or 'λ' in quantity or 'lambda' in quantity.lower()) else None
                continue
            if not active:continue
            if re.match(r'^\s*(?:Component Unreliability|A-\d|Figure\s+\d+|Table\s+\d+\.|[A-Z]-\d+\.)',line):
                if re.match(r'^\s*(?:Component Unreliability|Figure\s+\d+|Table\s+\d+\.|[A-Z]-\d+\.)',line):active=None
                continue
            m=ROW.match(line)
            if not m:continue
            code,mode,method,*nums=m.groups(); num,quantity,heading=active
            if mode not in DEMAND|HOURS:continue
            try:p5,p50,mean,p95,a,b=map(float,nums[:4]+nums[5:])
            except (ValueError,OverflowError):continue
            if (num,code,mode) in seen:continue
            seen.add((num,code,mode))
            dist=nums[4]
            unit='per demand' if mode in DEMAND else 'per hour'
            predicted=a/(a+b) if dist=='Beta' and a+b else a/b if b else 0
            rel=abs(predicted-mean)/mean if mean>0 else float('inf')
            issues=[]
            if not (a>0 and b>0 and mean>0 and 0<=p5<=p50<=p95 and p5<=mean<=p95):issues.append('invalid parameters or percentiles')
            if rel>0.035:issues.append('mean/shape mismatch')
            if unit=='per demand' and not 0<=mean<=1:issues.append('probability out of range')
            # The report's mixed-mode and mode-specific definitions vary. Only automatically
            # accept mode+distribution pairs verified against the source's exposure basis.
            if mode=='FTS' and dist!='Beta':issues.append('unexpected FTS distribution')
            if mode not in {'FTS','FTO','FTC','FTLR','FTR'}:issues.append('failure-mode exposure requires review')
            component_name=heading.rstrip('s') if heading.endswith('s') else heading
            if code=='EDG':component_name='Emergency Diesel Generator'
            rows.append(dict(component=component_name,pooling_group=code,failure_mode=mode,table=num,page=page_no,
                heading=heading,analysis=method,value=mean,unit=unit,parameter_type='failure probability' if unit=='per demand' else 'failure rate',
                distribution=dist,alpha=a if dist=='Beta' else None,beta=b if dist=='Beta' else None,
                shape=a if dist=='Gamma' else None,scale=1/b if dist=='Gamma' else None,
                p5=p5,p50=p50,p95=p95,gamma_rate=b if dist=='Gamma' else None,
                validation_relative_error=rel,issues=issues))
    return rows

def ingest_bulk(pdf,allow_automatic=True):
    pdf=Path(pdf); raw=pdf.read_bytes()
    if not raw.startswith(b'%PDF'):raise ValueError('Not a PDF')
    sha=hashlib.sha256(raw).hexdigest()
    intro=' '.join(subprocess.check_output(['pdftotext','-f','1','-l','5',str(pdf),'-'],text=True).split())
    if 'INL/EXT-21-65055' not in intro or '2020 Update' not in intro:raise ValueError('Unsupported document')
    init_reliability();init_studies();ROOT.mkdir(parents=True,exist_ok=True)
    archive=ROOT/(sha+'.pdf')
    if not archive.exists():archive.write_bytes(raw)
    url='local-upload://'+sha
    source='https://nrcoe.inl.gov/avgperf/'
    with connect() as c:
        c.execute('''INSERT INTO study_documents(url,title,listing_url,sha256,local_path,media_type,bytes,extraction_status)
        VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(url) DO UPDATE SET local_path=excluded.local_path''',
        (url,'Industry-Average Performance 2020 Update',source,sha,str(archive),'application/pdf',len(raw),'bulk-extracted'))
    extracted=extract_rows(archive); accepted=[];held=[]
    for r in extracted:
        # Automatic acceptance is restricted to the byte-identical vetted document and
        # unambiguous supported failure modes; other rows remain inspectable in review.
        ok=not r['issues'] and allow_automatic and sha in TESTED_SHA256
        status='validated' if ok else 'needs_review'
        rid=hashlib.sha256(json.dumps([sha,r['table'],r['pooling_group'],r['failure_mode']]).encode()).hexdigest()
        with connect() as c:
            c.execute('''INSERT INTO study_extraction_rows(id,document_url,sheet_or_page,row_number,raw_cells_json,candidate_component,candidate_failure_mode,candidate_value,candidate_unit,review_status)
            VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET raw_cells_json=excluded.raw_cells_json,
            review_status=CASE WHEN study_extraction_rows.review_status IN ('approved','rejected') THEN study_extraction_rows.review_status ELSE excluded.review_status END''',
            (rid,url,f"PDF page {r['page']} · Table {r['table']}",r['table'],json.dumps(r),r['component'],r['failure_mode'],str(r['value']),r['unit'],status))
        if not ok:held.append(r);continue
        d={k:'' for k in FIELDS}
        d.update(study_id='INL-EXT-21-65055-2020',title='Industry-Average Performance for Components and Initiating Events: 2020 Update',source_url=source,
            publisher='Idaho National Laboratory',study_period='2006–2020',population='U.S. commercial nuclear power plants',
            component=(r['component'] if r['pooling_group']=='EDG' else r['component']+' ['+r['pooling_group']+']'),failure_mode=r['failure_mode'],parameter_type=r['parameter_type'],value=str(r['value']),unit=r['unit'],
            distribution=r['distribution'],alpha=str(r['alpha'] or ''),beta=str(r['beta'] or ''),shape=str(r['shape'] or ''),scale=str(r['scale'] or ''),
            lower_bound=str(r['p5']),upper_bound=str(r['p95']),review_status='verified',
            notes=f"Pooling group {r['pooling_group']}; source SHA256 {sha}; PDF page {r['page']}; Table {r['table']}; median {r['p50']}; gamma rate {r['gamma_rate']}; mean validation error {r['validation_relative_error']:.5g}. Check failure-mode definition against report before modeling.")
        accepted.append(d)
    if accepted:
        with tempfile.NamedTemporaryFile('w',suffix='.csv',encoding='utf-8',newline='',delete=False) as f:
            w=csv.DictWriter(f,fieldnames=FIELDS);w.writeheader();w.writerows(accepted);temp=f.name
        try:result=ingest_csv(temp)
        finally:Path(temp).unlink(missing_ok=True)
        if result['rejected']:raise ValueError(str(result['errors']))
    return {'document_sha256':sha,'candidates':len(extracted),'automatically_validated':len(accepted),'needs_review':len(held),
        'components':len(set(r['component'] for r in extracted)),'tables':len(set(r['table'] for r in extracted)),
        'held_examples':held[:5]}

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('pdf');p.add_argument('--no-auto',action='store_true');a=p.parse_args()
    print(json.dumps(ingest_bulk(a.pdf,allow_automatic=not a.no_auto),indent=2))
