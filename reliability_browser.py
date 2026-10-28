"""Standalone, no-LLM reliability exploration and export."""
from flask import Blueprint, request, render_template, jsonify
from database import connect
from reliability_store import init, export_rows
bp=Blueprint('reliability_browser',__name__)

def query_rows(q='',component='',mode='',limit=500):
    init()
    sql='''SELECT p.*,s.title,s.source_url,s.publisher,s.study_period,s.population
      FROM reliability_parameters p JOIN reliability_studies s ON s.id=p.study_id
      WHERE p.review_status='verified' '''
    args=[]
    if q:
        sql+=' AND (p.component LIKE ? OR p.failure_mode LIKE ? OR p.notes LIKE ?)'
        args.extend(['%'+q+'%']*3)
    if component:sql+=' AND p.component LIKE ?';args.append('%'+component+'%')
    if mode:sql+=' AND p.failure_mode=?';args.append(mode)
    sql+=' ORDER BY p.component,p.failure_mode LIMIT ?';args.append(min(limit,10000))
    with connect() as c:return [dict(x) for x in c.execute(sql,args)]

@bp.get('/reliability')
def browser():
    q=request.args.get('q','').strip();component=request.args.get('component','').strip();mode=request.args.get('mode','').strip()
    rows=query_rows(q,component,mode)
    with connect() as c:
        components=[r[0] for r in c.execute("SELECT DISTINCT component FROM reliability_parameters WHERE review_status='verified' ORDER BY component")]
        modes=[r[0] for r in c.execute("SELECT DISTINCT failure_mode FROM reliability_parameters WHERE review_status='verified' ORDER BY failure_mode")]
    return render_template('reliability.html',rows=rows,q=q,component=component,mode=mode,components=components,modes=modes)

@bp.get('/api/reliability/browse')
def api_browser():
    return jsonify({'parameters':export_rows(query_rows(request.args.get('q',''),request.args.get('component',''),request.args.get('mode',''))),'note':'Published industry-average estimates are not rates derived from NRC Event Notifications.'})
