import argparse
from database import init_db, import_json
p=argparse.ArgumentParser();p.add_argument('json_file',nargs='?',default='events.json');a=p.parse_args()
init_db(); print(f'Imported/updated {import_json(a.json_file)} events.')
