"""Authenticated human review of staged study table rows.

No extraction is automatically considered verified. Reviewed parameters are imported
through the existing validated reliability_store ingestion path.
"""
import csv, io, json, os, secrets, tempfile
from pathlib import Path
from flask import Blueprint, abort, jsonify, render_template, request, session
from database import connect
from study_pipeline import init as init_studies
from reliability_store import init as init_reliability, ingest_csv, FIELDS

review_bp=Blueprint('review',__name__)

def authorized():
    token=os.getenv('NRC_REVIEW_TOKEN','')
    return bool(token) and secrets.compare_digest(str(session.get('review_token','')),token)

def csrf_guard():
    if not secrets.compare_digest(str(request.headers.get('X-CSRF-Token','')),str(session.get('csrf',''))):abort(403,description='Invalid CSRF token')

def guard():
    if not authorized():abort(403,description='Review access requires authentication and NRC_REVIEW_TOKEN configuration.')

@review_bp.route('/review/login',methods=['GET','POST'])
def login():
    if request.method=='POST':
        expected=os.getenv('NRC_REVIEW_TOKEN','')
        provided=request.form.get('token','')
        if expected and secrets.compare_digest(provided,expected):
            session.clear();session['review_token']=expected;session['csrf']=secrets.token_urlsafe(32)
            return __import__('flask').redirect('/review')
        return render_template('review_login.html',error='Invalid review token'),403
    return render_template('review_login.html',error=None)

@review_bp.post('/review/logout')
def logout():
    guard();csrf_guard();session.clear();return __import__('flask').redirect('/review/login')

@review_bp.get('/review')
def dashboard():
    guard();init_studies();init_reliability()
    page=max(1,int(request.args.get('page','1')))
    status=request.args.get('status','needs_review')
    if status not in ('needs_review','validated','approved','rejected','all'):status='needs_review'
    with connect() as c:
        where='' if status=='all' else 'WHERE r.review_status=?'
        args=() if status=='all' else (status,)
        count=c.execute('SELECT COUNT(*) FROM study_extraction_rows r '+where,args).fetchone()[0]
        rows=[dict(r) for r in c.execute('''SELECT r.*,d.title,d.local_path,d.sha256 FROM study_extraction_rows r
             JOIN study_documents d ON d.url=r.document_url '''+where+' ORDER BY d.title,r.sheet_or_page,r.row_number LIMIT 30 OFFSET ?',args+(30*(page-1),))]
        counts={r['review_status']:r['n'] for r in c.execute('SELECT review_status,COUNT(*) n FROM study_extraction_rows GROUP BY review_status')}
        docs=c.execute('SELECT COUNT(*) FROM study_documents').fetchone()[0]
    for row in rows:
        row['cells']=json.loads(row['raw_cells_json'])
    return render_template('review.html',csrf=session.get('csrf'),rows=rows,status=status,page=page,pages=max(1,(count+29)//30),counts=counts,docs=docs,total=count)

@review_bp.get('/api/review/row/<row_id>')
def get_row(row_id):
    guard();init_studies()
    with connect() as c:
        r=c.execute('''SELECT r.*,d.title,d.sha256 FROM study_extraction_rows r
                       JOIN study_documents d ON d.url=r.document_url WHERE r.id=?''',(row_id,)).fetchone()
    if not r:abort(404)
    result=dict(r);result['cells']=json.loads(result['raw_cells_json'])
    return jsonify(result)

@review_bp.post('/api/review/row/<row_id>')
def save_row(row_id):
    guard();csrf_guard();init_studies();data=request.get_json(silent=True) or {}
    action=data.get('action')
    if action not in ('approve','reject'):return jsonify(error='Invalid action'),400
    with connect() as c:
        row=c.execute('''SELECT r.*,d.title,d.sha256 FROM study_extraction_rows r
                         JOIN study_documents d ON d.url=r.document_url WHERE r.id=?''',(row_id,)).fetchone()
        if not row:return jsonify(error='Row not found'),404
        if action=='reject':
            c.execute("UPDATE study_extraction_rows SET review_status='rejected' WHERE id=?",(row_id,))
            return jsonify(ok=True,status='rejected')
        payload={k:str(data.get(k,'') or '').strip() for k in FIELDS}
        payload['source_url']=row['document_url']
        payload['title']=payload['title'] or row['title']
        payload['study_id']=payload['study_id'] or row['sha256'][:24]
        payload['review_status']='verified'
        required=['component','failure_mode','parameter_type','value','unit']
        missing=[k for k in required if not payload[k]]
        if missing:return jsonify(error='Missing: '+', '.join(missing)),400
        # Preserve exact document and table location in provenance notes.
        provenance=f"Source SHA256: {row['sha256']}; table: {row['sheet_or_page']}; row: {row['row_number']}; extraction ID: {row_id}"
        payload['notes']=(payload['notes']+' | '+provenance).strip(' |')
        output=io.StringIO();w=csv.DictWriter(output,fieldnames=FIELDS);w.writeheader();w.writerow(payload)
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w',suffix='.csv',encoding='utf-8',newline='',delete=False) as f:
            f.write(output.getvalue());path=f.name
        try:result=ingest_csv(path)
        finally:os.unlink(path)
        if result['rejected']:return jsonify(error=result['errors'][0]['error']),400
        c.execute("UPDATE study_extraction_rows SET review_status='approved' WHERE id=?",(row_id,))
    return jsonify(ok=True,status='approved',imported=result['imported'])


@review_bp.post('/review/upload')
def upload_study():
    guard()
    token=request.form.get('csrf','')
    if not secrets.compare_digest(token,str(session.get('csrf',''))):abort(403)
    f=request.files.get('study')
    if not f or not f.filename.lower().endswith('.pdf'):return jsonify(error='Select a PDF file'),400
    # Upload limit is also enforced at the Flask app level.
    with tempfile.NamedTemporaryFile(suffix='.pdf',delete=False) as tmp:
        path=tmp.name
        while True:
            chunk=f.stream.read(65536)
            if not chunk:break
            tmp.write(chunk)
    try:
        from bulk_reliability_import import ingest_bulk
        result=ingest_bulk(path)
        return __import__('flask').redirect('/review?status=all&imported='+str(result['automatically_validated']))
    except Exception as exc:
        return jsonify(error=str(exc)),400
    finally:Path(path).unlink(missing_ok=True)
