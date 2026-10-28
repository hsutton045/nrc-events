"""Explainable event-classification to published-parameter candidate matching.

Never asserts a publication estimate is a plant-specific event rate.
Only exact or explicit category aliases; ambiguous failures remain unmatched.
"""
import re
from flask import Blueprint, jsonify, request
from anylogic_export import find_parameters, model_record, ALIASES

bp = Blueprint('event_reliability', __name__)

MODE_PHRASES = {
    'FTS': (r'\bfailure to start\b', r'\bfailed to start\b', r'\bfail to start\b', r'\bfts\b'),
    'FTR': (r'\bfailure to run\b', r'\bfailed while running\b', r'\bftr\b'),
    'FTLR': (r'\bfailure to load and run\b', r'\bftlr\b'),
    'FTO': (r'\bfailure to open\b', r'\bfailed to open\b', r'\bfto\b'),
    'FTC': (r'\bfailure to close\b', r'\bfailed to close\b', r'\bftc\b'),
}

# Classifier descriptions may contain unrelated context; prefer an explicit failure-mode field.
def resolve_mode(mode):
    value=(mode or '').strip()
    if not value: return None
    if value.upper() in MODE_PHRASES: return value.upper()
    matches=[code for code, patterns in MODE_PHRASES.items() if any(re.search(p,value,re.I) for p in patterns)]
    return matches[0] if len(matches)==1 else None

def resolve_component(component):
    value=' '.join((component or '').lower().split())
    if not value: return None
    if value in ALIASES: return value
    # No substring / generic fallback: require explicit category.
    return None

def match_event(component, failure_mode):
    category=resolve_component(component)
    mode=resolve_mode(failure_mode)
    if not category or not mode:
        return {'matched':0,'parameters':[], 'component_category':category,
                'failure_mode':mode,'status':'unmatched',
                'reason':'Component category or failure mode is ambiguous or unsupported; manual selection required.'}
    rows=find_parameters(category,mode,limit=100)
    return {'matched':len(rows),'parameters':[model_record(r) for r in rows],
            'component_category':category,'failure_mode':mode,
            'status':'candidate_match' if rows else 'unmatched',
            'reason':'Exact published category and failure-mode candidate; verify applicability to the event and model.' if rows else 'No verified parameter for this exact category and mode.'}

@bp.get('/api/reliability/match-event')
def match_event_api():
    return jsonify(match_event(request.args.get('component',''),request.args.get('failure_mode','')))
