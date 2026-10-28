from __future__ import annotations
import os, re, time, threading, uuid
from flask import Flask, abort, jsonify, render_template, request, Response
from database import init_db, get_event, database_last_updated, search_keyword
from ai_search import semantic_candidates, classify, stats, generate_report
from reliability import SOURCES
from reliability_store import init as init_reliability, search as search_reliability, export_rows

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 40 * 1024 * 1024
app.secret_key = os.getenv("NRC_SESSION_SECRET") or os.urandom(32)
from review_interface import review_bp
app.register_blueprint(review_bp)
from reliability_browser import bp as reliability_bp
app.register_blueprint(reliability_bp)
from anylogic_export import bp as anylogic_bp
app.register_blueprint(anylogic_bp)
from event_reliability import bp as event_reliability_bp, match_event
app.register_blueprint(event_reliability_bp)
init_db()
init_reliability()

_jobs = {}
_jobs_lock = threading.Lock()


def snippet(text, query='', radius=180):
    flat = re.sub(r'\s+', ' ', text or '').strip()
    terms = query.split(); pos = -1; term = ''
    for term in terms:
        pos = flat.lower().find(term.lower())
        if pos >= 0: break
    if pos < 0: return flat[:radius*2] + ('...' if len(flat) > radius*2 else '')
    a=max(0,pos-radius); b=min(len(flat),pos+len(term)+radius)
    return ('...' if a else '') + flat[a:b] + ('...' if b < len(flat) else '')


def decorate(event, query=''):
    event=dict(event); event['display_title']=event.get('title') or 'Event'; event['snippet']=snippet(event.get('event_text',''),query)
    return event


def limits(depth):
    return (1000, 1000) if depth == 'comprehensive' else (100, 80)


def update_job(job_id, **changes):
    with _jobs_lock:
        if job_id in _jobs:
            _jobs[job_id].update(changes)
            _jobs[job_id]['elapsed'] = round(time.time() - _jobs[job_id]['started_at'], 1)


def run_ai_job(job_id, query, start, end, depth):
    candidate_limit, classify_limit = limits(depth)
    def progress(**changes): update_job(job_id, **changes)
    try:
        candidates = semantic_candidates(query, start, end, candidate_limit, progress=progress)
        classified, status = classify(query, candidates, classify_limit, return_status=True, progress=progress)
        relevant = sum(bool(x.get('relevant')) for x in classified)
        update_job(job_id, state='done', stage='done', message='Search complete', percent=100,
                   candidates=len(candidates), completed=len(classified), relevant=relevant,
                   failed=status.get('failed', 0))
    except Exception as exc:
        update_job(job_id, state='error', stage='error', message=str(exc), error=str(exc))


@app.post('/api/ai-search/start')
def start_ai_search():
    data=request.get_json(silent=True) or request.form
    q=str(data.get('keywords','')).strip()
    if not q: return jsonify({'error':'AI Search requires a research question.'}),400
    depth=str(data.get('depth','standard'))
    if depth not in ('standard','comprehensive'): depth='standard'
    job_id=uuid.uuid4().hex
    candidate_limit, classify_limit=limits(depth)
    with _jobs_lock:
        _jobs[job_id]={'state':'running','stage':'starting','message':'Starting AI search…','percent':1,
            'query':q,'depth':depth,'candidates':0,'requested':classify_limit,'completed':0,'relevant':0,
            'cache_hits':0,'failed':0,'started_at':time.time(),'elapsed':0}
    threading.Thread(target=run_ai_job,args=(job_id,q,data.get('start_date') or None,data.get('end_date') or None,depth),daemon=True).start()
    return jsonify({'job_id':job_id})


@app.get('/api/ai-search/status/<job_id>')
def ai_search_status(job_id):
    with _jobs_lock: job=dict(_jobs.get(job_id,{}))
    if not job: return jsonify({'error':'Search job not found.'}),404
    job.pop('started_at',None)
    return jsonify(job)


@app.route('/')
def index():
    started=time.perf_counter(); mode='ai'; q=request.args.get('keywords','').strip()
    exclude=request.args.get('exclude_keywords','').strip(); start=request.args.get('start_date','').strip() or None; end=request.args.get('end_date','').strip() or None
    # Query-text retrieval is contextual only, not event-to-parameter matching.
    matched_parameters=search_reliability(q) if q else []
    event_parameter_matches=[]
    parameter_warning=None
    results=[]; error=''; warning=''; ai_stats=None; report=None; classification_status=None; semantic_count=0; classify_limit=0
    search_depth=request.args.get('depth','standard'); search_depth=search_depth if search_depth in ('standard','comprehensive') else 'standard'
    if q or exclude or start or end:
        try:
            if mode=='ai':
                if not q: raise ValueError('AI Search requires a research question.')
                candidate_limit,classify_limit=limits(search_depth)
                candidates=semantic_candidates(q,start,end,candidate_limit); semantic_count=len(candidates)
                classified,classification_status=classify(q,candidates,classify_limit,return_status=True)
                ai_stats=stats(classified); results=[decorate(x,q) for x in classified]; relevant=[x for x in classified if bool(x.get('relevant'))]
                # Only attach explicit component + mode matches to classified relevant events.
                event_parameter_matches=[{'event_number':e.get('event_number'), 'component':e.get('component'),
                                          'failure_mode':e.get('failure_mode'), **match_event(e.get('component'),e.get('failure_mode'))}
                                         for e in relevant]
                # Preserve existing research report behavior; never substitute matches for exposure-normalized rates.
                if classification_status['failed']:
                    warning=(f"{classification_status['classified']} of {classification_status['requested']} selected candidates were classified. "
                             f"{classification_status['failed']} could not be classified after retries. Successful results were retained and cached.")
                if request.args.get('report')=='1': report=generate_report(q,relevant,ai_stats, matched_parameters)

        except Exception as exc: error=str(exc)
        finally:
            if mode=='ai': print(f"[AI SEARCH] Request finished in {time.perf_counter()-started:.1f}s.",flush=True)
    return render_template('index.html',mode=mode,keywords=q,exclude_keywords=exclude,start_date=start or '',end_date=end or '',results=results,
        last_updated=database_last_updated(),search_depth=search_depth,ai_stats=ai_stats,report=report,error=error,warning=warning,
        classification_status=classification_status,semantic_count=semantic_count,classify_limit=classify_limit,
        reliability_sources=SOURCES,reliability_parameters=matched_parameters,event_parameter_matches=event_parameter_matches,parameter_warning=parameter_warning,ai_enabled=bool(os.getenv('OPENAI_API_KEY')))

@app.get('/api/reliability/export')
def reliability_export():
    import csv, io, json
    q=request.args.get('q','').strip()
    if not q: return jsonify({'error':'A research query is required.'}),400
    rows=export_rows(search_reliability(q,limit=10000))
    if request.args.get('format')=='csv':
        output=io.StringIO(); fields=list(rows[0]) if rows else ['component','failure_mode','value','unit','distribution','source_url']
        writer=csv.DictWriter(output,fieldnames=fields);writer.writeheader();writer.writerows(rows)
        return Response(output.getvalue(),mimetype='text/csv',headers={'Content-Disposition':'attachment; filename="reliability_parameters.csv"'})
    return jsonify({'query':q,'parameters':rows,'warning':'Verify parameter applicability, review status, units and uncertainty before simulation.'})

@app.route('/event/<event_number>')
def event_detail(event_number):
    event=get_event(event_number)
    if not event: abort(404)
    return render_template('event.html',event=event,back=request.referrer or '/')

if __name__=='__main__': app.run(debug=True)
