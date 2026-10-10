"""Reject reduced pools that lose frozen gold before spending inference time."""
import json
from pathlib import Path
from app.museum.rerank_selection import protected_pool
from app.museum.config import ROOT


def main():
    source=ROOT/'eval/private/rerank-latency-input-v1.json'
    frozen=json.loads(source.read_bytes());assert frozen['complete']
    report={}
    for limit in (8,12,16):
        rows=[]
        for row in frozen['rows']:
            req=row['request']
            if not req:continue
            selected,audit=protected_pool(req['query'],req['candidates'],req['sources'],row['fallback_ids'],limit)
            before=bool(set(row['gold'])&{c['source_id'] for c in req['candidates']})
            after=bool(set(row['gold'])&{c['source_id'] for c in selected})
            rows.append(dict(id=row['id'],before_hit=before,after_hit=after,count=len(selected),audit=audit))
        report[str(limit)]=dict(rows=rows,lost=[r['id'] for r in rows if r['before_hit'] and not r['after_hit']],
            candidate_pairs=sum(r['count'] for r in rows),overflows=sum(r['audit']['overflow'] for r in rows))
    with (ROOT/'eval/private/rerank-pool-v1.json').open('x',encoding='utf-8') as f:json.dump(report,f,ensure_ascii=False,indent=2)
    print(json.dumps({k:{a:b for a,b in v.items() if a!='rows'} for k,v in report.items()},ensure_ascii=False))


if __name__=='__main__':main()
