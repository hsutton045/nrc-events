"""Auditable, unit-safe component reliability mapping and AnyLogic input exports.

No inferred repair distributions, plant-specific rates, or event-derived rates.
"""
import csv, io, json, re
from flask import Blueprint, request, jsonify, Response
from reliability_browser import query_rows

bp = Blueprint('anylogic_export', __name__)

# Query aliases only aid discovery. They do not change the published component category.
ALIASES = {
    'edg': ('emergency diesel generator',),
    'emergency diesel generator': ('emergency diesel generator',),
    'diesel generator': ('emergency diesel generator',),
    'rcp': ('reactor coolant pump',),
    'reactor coolant pump': ('reactor coolant pump',),
    'motor driven pump': ('motor driven pump',),
    'mdp': ('motor driven pump',),
    'mov': ('motor operated valve',),
    'motor operated valve': ('motor operated valve',),
    'aov': ('air operated valve',),
    'air operated valve': ('air operated valve',),
    'breaker': ('circuit breaker',),
}
MODE_LABELS = {
    'FTS':'Failure to start', 'FTR':'Failure to run',
    'FTLR':'Failure to load and run', 'FTO':'Failure to open',
    'FTC':'Failure to close', 'FTOC':'Failure to open or close',
    'FTOP':'Failure to operate', 'SOP':'Spurious operation',
}
STOP = {'failure','failures','reliability','rates','rate','of','the','a','an','for','find','show','and','events','event','component','components'}

def tokens(value):
    return set(re.findall(r'[a-z0-9]+', (value or '').lower())) - STOP

def find_parameters(component='', mode='', limit=100):
    """Conservative matching; return candidates with an explicit match basis.

    Never map reactor coolant pumps to generic pumps automatically.
    """
    term=component.strip().lower()
    aliases=ALIASES.get(term, (term,))
    all_rows=query_rows(limit=10000)
    matched=[]
    for r in all_rows:
        name=r['component'].lower()
        # Exact component/pooling-group match, or explicit recognized alias.
        component_match=not term or any(a == name or name.startswith(a+' [') for a in aliases)
        if not component_match:
            continue
        if mode and r['failure_mode'].upper()!=mode.upper():
            continue
        matched.append(dict(r, match_basis='exact component category' if term else 'unfiltered',
                            applicability='Industry-average pooling group; verify applicability to the modeled component.'))
        if len(matched)>=limit:break
    return matched

def model_record(r):
    dist=(r.get('distribution') or '').lower()
    unit=(r.get('unit') or '').lower()
    result={
        'parameter_id':r['id'], 'component_category':r['component'],
        'failure_mode_code':r['failure_mode'],
        'failure_mode_label':MODE_LABELS.get(r['failure_mode'], 'Refer to source failure-mode definition'),
        'parameter_type':r['parameter_type'], 'mean':r['value'],
        'unit':r['unit'], 'distribution':r['distribution'],
        'distribution_parameters':{},
        'p05':r.get('lower_bound'), 'p95':r.get('upper_bound'),
        'exposure':r.get('exposure'), 'exposure_unit':r.get('exposure_unit'),
        'repair_distribution':None, 'repair_time':None,
        'study_id':r['study_id'], 'source_title':r.get('title'),
        'source_url':r.get('source_url'), 'study_period':r.get('study_period'),
        'source_notes':r.get('notes'), 'review_status':r['review_status'],
        'match_basis':r.get('match_basis','library selection'),
        'applicability_warning':r.get('applicability','Verify component pooling group, duty cycle, failure mode, and plant applicability.'),
    }
    if dist=='beta':
        result['distribution_parameters']={'alpha':r['alpha'],'beta':r['beta']}
    elif dist=='gamma':
        result['distribution_parameters']={'shape':r['shape'],'scale':r['scale'], 'parameterization':'shape-scale'}
    if unit=='per demand':
        result['simulation_semantics']='Sample a probability per demand; evaluate on each modeled demand.'
    elif unit=='per hour':
        result['simulation_semantics']='Sample a rate in inverse hours; conditional constant-rate time-to-failure may be modeled using exponential waiting time if justified.'
    else:
        result['simulation_semantics']='Units not recognized; check source before modeling.'
    return result

def build_export(component='',mode=''):
    rows=find_parameters(component,mode)
    return {
        'schema_version':'1.0', 'source':'Published industry-average reliability parameters',
        'filters':{'component':component,'failure_mode':mode},
        'modeling_warnings':[
            'Industry-average parameters are not plant-specific estimates.',
            'Uncertainty distributions describe epistemic uncertainty in failure probabilities or rates; do not use their sampled values directly as time-to-failure samples.',
            'Per-demand probabilities and per-hour failure rates are not interchangeable.',
            'Repair rates and maintenance durations are not supplied and have not been inferred.',
            'Event Notification counts are not exposure-normalized failure rates.',
        ],
        'parameters':[model_record(r) for r in rows],
    }

@bp.get('/api/reliability/match')
def match():
    component=request.args.get('component','').strip()
    mode=request.args.get('mode','').strip()
    if not component:return jsonify({'error':'component is required'}),400
    rows=find_parameters(component,mode)
    return jsonify({'component':component,'failure_mode':mode,'matched':len(rows),
                    'parameters':[model_record(r) for r in rows],
                    'warning':'Matching does not establish plant-specific applicability.'})

@bp.get('/api/reliability/anylogic')
def export():
    component=request.args.get('component','').strip()
    mode=request.args.get('mode','').strip()
    data=build_export(component,mode)
    fmt=request.args.get('format','json').lower()
    if fmt=='json':
        return Response(json.dumps(data,indent=2,ensure_ascii=False),mimetype='application/json',
                        headers={'Content-Disposition':'attachment; filename="anylogic_reliability.json"'})
    if fmt!='csv':return jsonify({'error':'format must be json or csv'}),400
    fields=['parameter_id','component_category','failure_mode_code','failure_mode_label','parameter_type',
            'mean','unit','distribution','alpha','beta','shape','scale','p05','p95','exposure','exposure_unit',
            'repair_distribution','repair_time','study_id','source_title','source_url','study_period','review_status','source_notes']
    output=io.StringIO();writer=csv.DictWriter(output,fieldnames=fields);writer.writeheader()
    for r in data['parameters']:
        params=r['distribution_parameters']
        writer.writerow({k:(params.get(k) if k in ('alpha','beta','shape','scale') else r.get(k)) for k in fields})
    return Response(output.getvalue(),mimetype='text/csv',headers={'Content-Disposition':'attachment; filename="anylogic_reliability.csv"'})
