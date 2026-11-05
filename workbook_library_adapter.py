"""Read-only adapter for separately staged 2020 SPAR workbook parameters.

Does not alter reliability_parameters or workbook_reliability_records.
"""
import json
from database import connect

ALLOWED = ('validated', 'approved')


def available(connection):
    return connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='workbook_reliability_records'").fetchone() is not None


def workbook_rows():
    with connect() as c:
        if not available(c):
            return []
        records = [dict(r) for r in c.execute('''SELECT * FROM workbook_reliability_records
            WHERE review_status IN ('validated','approved') ORDER BY sheet,excel_row''')]
    result=[]
    for r in records:
        # Do not model records with unresolved units or absent distribution parameters.
        if not r.get('unit') or r.get('mean') is None:
            continue
        try:
            warnings=json.loads(r.get('warnings_json') or '[]')
        except (ValueError,TypeError):
            warnings=['Unparseable source warnings']
        if warnings:
            # Manually approved records may carry documented caveats, but should not
            # be silently interpreted as unqualified simulation-ready inputs.
            if r['review_status'] != 'approved':
                continue
        notes='2020 SPAR workbook; sheet '+str(r['sheet'])+'; Excel row '+str(r['excel_row'])+'; source code '+str(r['code'])+'; source status '+str(r['review_status'])
        if r.get('description'): notes+='; description: '+str(r['description'])
        if r.get('failures') is not None: notes+='; observed failures: '+str(r['failures'])
        if r.get('component_count') is not None: notes+='; component count: '+str(r['component_count'])
        if warnings: notes+='; review warnings: '+', '.join(map(str,warnings))
        result.append(dict(
            id='workbook:'+r['id'],study_id='INL-EXT-21-65055-2020-WORKBOOK',
            title='2020 SPAR Parameter Estimates workbook',source_url=r['source_url'],
            publisher='Idaho National Laboratory',study_period=r.get('study_period') or 'See source workbook',
            population='U.S. commercial nuclear power plants',
            component=r.get('component') or r['code'],failure_mode=r.get('failure_mode') or r['code'],
            parameter_type=r['parameter_type'],value=r['mean'],unit=r['unit'],
            distribution=r['distribution'],alpha=r['alpha'],beta=r['beta'],
            shape=r['shape'],scale=r['scale'],lower_bound=r['p05'],upper_bound=r['p95'],
            exposure=r['exposure'],exposure_unit=r['exposure_unit'],notes=notes,
            review_status='verified' if r['review_status']=='approved' else 'validated',
            source_kind='workbook',source_review_status=r['review_status'],
            category=r['category'],source_sheet=r['sheet'],source_excel_row=r['excel_row'],
            observed_failures=r['failures'],component_count=r['component_count'],
            source_code=r['code'],source_warnings=warnings,
        ))
    return result
