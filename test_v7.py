"""Isolated tests for unit-safe AnyLogic export and conservative component matching."""
import os, tempfile, pathlib, importlib

def run(pdf=None):
    with tempfile.TemporaryDirectory() as temp:
        os.environ['NRC_DB_PATH']=str(pathlib.Path(temp)/'test.db')
        import database
        importlib.reload(database)
        from reliability_store import ingest_csv, FIELDS
        import csv
        sample=[]
        for component,mode,unit,dist in [
            ('Emergency Diesel Generator','FTS','per demand','Beta'),
            ('Emergency Diesel Generator','FTR','per hour','Gamma'),
            ('Motor Driven Pump [MDP]','FTS','per demand','Beta')]:
            d={k:'' for k in FIELDS}
            d.update(study_id='test',title='Test source',source_url='https://example.org/report',component=component,
                     failure_mode=mode,parameter_type='failure probability' if unit=='per demand' else 'failure rate',
                     value='0.002',unit=unit,distribution=dist,review_status='verified')
            d.update(alpha='2',beta='998') if dist=='Beta' else d.update(shape='2',scale='0.001')
            sample.append(d)
        path=pathlib.Path(temp)/'input.csv'
        with path.open('w',newline='') as f:
            w=csv.DictWriter(f,fieldnames=FIELDS);w.writeheader();w.writerows(sample)
        assert ingest_csv(path)['imported']==3
        from anylogic_export import find_parameters,build_export
        assert len(find_parameters('edg'))==2
        assert len(find_parameters('reactor coolant pump'))==0, 'Unsafe generic pump match'
        assert len(find_parameters('motor driven pump'))==1
        assert len(find_parameters('edg','FTR'))==1
        data=build_export('edg')
        assert len(data['parameters'])==2
        assert all(r['repair_time'] is None for r in data['parameters'])
        assert {r['unit'] for r in data['parameters']}=={'per demand','per hour'}
        from app import app
        client=app.test_client()
        assert client.get('/api/reliability/match?component=edg').json['matched']==2
        assert client.get('/api/reliability/match?component=rcp').json['matched']==0
        assert client.get('/api/reliability/anylogic?component=edg&format=json').status_code==200
        csv_response=client.get('/api/reliability/anylogic?component=edg&format=csv')
        assert csv_response.status_code==200 and len(csv_response.data.splitlines())==3
        assert client.get('/reliability').status_code==200
        print('PASS: exact/alias component matching, mode filters, uncertainty and unit-safe exports, Flask endpoints')
if __name__=='__main__':run()
