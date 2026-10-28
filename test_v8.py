"""Regression suite for v8 matching, exports and Flask routes; isolated temporary SQLite."""
import csv, importlib, os, pathlib, tempfile

def main():
    with tempfile.TemporaryDirectory() as folder:
        os.environ['NRC_DB_PATH']=str(pathlib.Path(folder)/'isolated.sqlite')
        import database
        importlib.reload(database)
        from reliability_store import FIELDS, ingest_csv
        rows=[]
        for component, mode, unit, dist, value in [
            ('Emergency Diesel Generator','FTS','per demand','Beta','0.002'),
            ('Emergency Diesel Generator','FTR','per hour','Gamma','0.001'),
            ('Motor Driven Pump [MDP]','FTS','per demand','Beta','0.002')]:
            r={k:'' for k in FIELDS}
            r.update(study_id='test',title='Synthetic test data',source_url='https://example.org/test',component=component,
                     failure_mode=mode,unit=unit,value=value,distribution=dist,
                     parameter_type='failure probability' if unit=='per demand' else 'failure rate',review_status='verified')
            if dist=='Beta':r.update(alpha='2',beta='998')
            else:r.update(shape='2',scale='0.0005')
            rows.append(r)
        csvfile=pathlib.Path(folder)/'parameters.csv'
        with csvfile.open('w',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=FIELDS);writer.writeheader();writer.writerows(rows)
        assert ingest_csv(csvfile)['imported']==3
        from event_reliability import match_event
        assert match_event('EDG','Failure to start')['matched']==1
        assert match_event('EDG','FTR')['matched']==1
        assert match_event('EDG','not sure')['matched']==0
        assert match_event('Reactor coolant pump','FTS')['matched']==0
        assert match_event('pump','FTS')['matched']==0
        assert match_event('EDG','')['matched']==0
        from anylogic_export import build_export
        data=build_export('edg');assert len(data['parameters'])==2
        assert {p['unit'] for p in data['parameters']}=={'per demand','per hour'}
        assert all(p['repair_time'] is None for p in data['parameters'])
        from app import app
        client=app.test_client()
        assert client.get('/').status_code==200
        assert client.get('/reliability').status_code==200
        assert client.get('/api/reliability/match-event?component=edg&failure_mode=FTS').json['matched']==1
        assert client.get('/api/reliability/match-event?component=rcp&failure_mode=FTS').json['matched']==0
        assert client.get('/api/reliability/anylogic?component=edg&format=json').status_code==200
        assert client.get('/api/reliability/anylogic?component=edg&format=csv').status_code==200
        print('PASS v8: event matching, non-substitution, ambiguity guard, units, exports, Flask endpoints')

if __name__=='__main__':main()
