"""Read-only combined PDF and workbook reliability parameter library."""
from flask import Blueprint, request, render_template, jsonify
from database import connect
from reliability_store import init
from workbook_library_adapter import workbook_rows

bp=Blueprint('reliability_browser',__name__)

def query_rows(q='',component='',mode='',limit=500):
    init()
    with connect() as c:
        pdf=[dict(x) for x in c.execute("""SELECT p.*,s.title,s.source_url,s.publisher,s.study_period,s.population
            FROM reliability_parameters p JOIN reliability_studies s ON s.id=p.study_id
            WHERE p.review_status='verified' """)]
    for r in pdf:
        r['source_kind']='pdf'
        r['source_review_status']='verified'
    rows=pdf+workbook_rows()
    if q:
        needle=q.lower()
        rows=[r for r in rows if any(needle in str(r.get(k) or '').lower()
             for k in ('component','failure_mode','notes','title','parameter_type'))]
    if component:
        rows=[r for r in rows if component.lower() in (r.get('component') or '').lower()]
    if mode:
        rows=[r for r in rows if (r.get('failure_mode') or '').lower()==mode.lower()]
    rows.sort(key=lambda r:((r.get('component') or '').lower(),(r.get('failure_mode') or '').lower(),r['source_kind']))
    return rows[:min(max(int(limit),1),10000)]

@bp.get('/reliability')
def browser():
    q=request.args.get('q','').strip()
    component=request.args.get('component','').strip()
    mode=request.args.get('mode','').strip()
    rows=query_rows(q,component,mode)
    all_rows=query_rows(limit=10000)
    components=sorted({r['component'] for r in all_rows if r.get('component')})
    modes=sorted({r['failure_mode'] for r in all_rows if r.get('failure_mode')})
    return render_template('reliability.html',rows=rows,q=q,component=component,mode=mode,components=components,modes=modes)

@bp.get('/api/reliability/browse')
def api_browser():
    rows=query_rows(request.args.get('q',''),request.args.get('component',''),request.args.get('mode',''),limit=10000)
    return jsonify({'parameters':rows,'note':'Includes verified PDF and validated/approved workbook estimates. Distinct source records may represent the same estimate; do not treat as independent observations.'})
