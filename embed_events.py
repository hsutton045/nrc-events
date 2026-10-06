import argparse
from ai_search import embed_missing
p=argparse.ArgumentParser();p.add_argument('--force',action='store_true');p.add_argument('--batch-size',type=int,default=64);a=p.parse_args();print(f'Embedded {embed_missing(a.batch_size,a.force)} events.')
