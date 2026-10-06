from __future__ import annotations
import argparse,re,time,json
from datetime import datetime,date
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup
from database import init_db, connect, upsert_events, set_meta, clean_event_text

YEAR='https://www.nrc.gov/reading-rm/doc-collections/event-status/event/{year}/index.html'
HEADERS={'User-Agent':'Mozilla/5.0 (compatible; NRCEventResearch/2.0)'}
def fetch(u):r=requests.get(u,headers=HEADERS,timeout=(10,30));r.raise_for_status();return r.text
def links(year):
 s=BeautifulSoup(fetch(YEAR.format(year=year)),'html.parser'); base=YEAR.format(year=year)
 return list(dict.fromkeys(urljoin(base,a['href'].strip()) for a in s.find_all('a',href=True) if re.search(r'\d{8}en(?:\.html)?$',a['href'].strip())))
def linkdate(u):
 m=re.search(r'/(\d{8})en(?:\.html)?$',u)
 try:return datetime.strptime(m.group(1),'%Y%m%d').date() if m else None
 except:return None
def clean(t):
 t=t.replace('\xa0',' ');t=re.sub(r'\r\n?','\n',t);t=re.sub(r'[ \t]+',' ',t);t=re.sub(r'\n{3,}','\n\n',t);return t.strip()
def field(p,t,flags=0):
 m=re.search(p,t,flags);return clean(m.group(1)) if m else ''
def parse_page(u):
 s=BeautifulSoup(fetch(u),'html.parser'); txt=clean(s.get_text('\n'))
 rd=field(r'Event Notification Report for ([A-Za-z]+ \d{1,2}, \d{4})',txt)
 starts=[m.start() for m in re.finditer(r'(?m)^Event Number:\s*',txt)]; out=[]
 for i,st in enumerate(starts):
  block=txt[st:starts[i+1] if i+1<len(starts) else len(txt)].strip()
  num=field(r'Event Number:\s*(.+)',block)
  if not num:continue
  et=clean_event_text(field(r'Event Text\s*(.*)',block,re.S)); lines=[x.strip() for x in et.splitlines() if x.strip()]
  fallback=field(r'Notification Date:.*?\n(.*?)\n',block,re.S)
  out.append({'event_number':num,'report_date':rd,'facility':field(r'Facility:\s*(.+)',block),'state':field(r'State:\s*([A-Z]{2})',block),'title':next((x for x in lines[:8] if len(x)<=160),fallback),'event_text':et,'report_url':u})
 return out
def latest():
 with connect() as c:
  r=c.execute("SELECT MAX(report_date_iso) d FROM events").fetchone();return date.fromisoformat(r['d']) if r and r['d'] else None
def main():
 p=argparse.ArgumentParser();p.add_argument('--full',action='store_true');p.add_argument('--start-year',type=int,default=1999);p.add_argument('--end-year',type=int,default=datetime.now().year);p.add_argument('--delay',type=float,default=.5);p.add_argument('--export-json',default='',help='Optional JSON export path after update');a=p.parse_args();init_db()
 since=None if a.full else latest(); start=a.start_year if a.full or not since else since.year
 total=0
 for y in range(start,a.end_year+1):
  try: ls=links(y)
  except Exception as e:print(f'{y}: index error: {e}');continue
  if since:ls=[u for u in ls if linkdate(u) is None or linkdate(u)>=since]
  print(f'{y}: {len(ls)} daily pages')
  for n,u in enumerate(ls,1):
   try:
    ev=parse_page(u);upsert_events(ev);total+=len(ev);print(f'[{y} {n}/{len(ls)}] {len(ev)} events')
   except Exception as e:print(f'ERROR {u}: {e}')
   time.sleep(a.delay)
 set_meta('last_updated',datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
 if a.export_json:
  with connect() as c: rows=[dict(r) for r in c.execute('SELECT event_number,report_date,facility,state,title,event_text,report_url FROM events ORDER BY report_date_iso DESC')]
  with open(a.export_json,'w',encoding='utf-8') as f: json.dump(rows,f,indent=2,ensure_ascii=False)
  print(f'Exported {len(rows)} events to {a.export_json}')
 print(f'Fetched/updated {total} event records.')
if __name__=='__main__':main()
