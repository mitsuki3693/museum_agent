"""Compare frozen runs without changing labels or treating unlabelled as wrong."""
import argparse
import json
import hashlib
from pathlib import Path

from evaluate_bilingual_retrieval import private_path
from app.museum.config import ROOT


def main(baseline,candidate,output):
    a=json.loads(private_path(baseline).read_bytes());b=json.loads(private_path(candidate).read_bytes())
    assert a['complete'] and b['complete'] and a['input_sha256']==b['input_sha256']
    frozen=ROOT/'eval/private/rerank-latency-input-v1.json'
    assert hashlib.sha256(frozen.read_bytes()).hexdigest()==a['input_sha256']
    cases={r['id']:r for r in json.loads(frozen.read_bytes())['rows']}
    before={r['id']:r for r in a['rows'] if r['round']==1}
    rows=[]
    for new in b['rows']:
        old=before[new['id']]
        assert set(old['ids'])==set(new['ids'])
        if a['budget']==b['budget']:
            assert {e['source_id']:e['passage_sha256'] for e in old['evidence']}=={e['source_id']:e['passage_sha256'] for e in new['evidence']}
        rows.append(dict(id=new['id'],scored=bool(cases[new['id']]['gold']),before_rank=old['rank'],after_rank=new['rank'],
            lost_top5=old['rank'] is not None and new['rank'] is None,
            worse_rank=old['rank'] is not None and (new['rank'] or 99)>old['rank'],
            top5_same_order=old['ids'][:5]==new['ids'][:5],top5_same_set=set(old['ids'][:5])==set(new['ids'][:5])))
    report=dict(complete=True,baseline=str(baseline.name),candidate=str(candidate.name),rows=rows,
        summary=dict(paired=len(rows),scored=sum(r['scored'] for r in rows),lost_top5=[r['id'] for r in rows if r['lost_top5']],
            worse_rank=[r['id'] for r in rows if r['worse_rank']],
            before_top5=sum(r['before_rank'] is not None for r in rows),after_top5=sum(r['after_rank'] is not None for r in rows),
            same_top5_order=sum(r['top5_same_order'] for r in rows),same_top5_set=sum(r['top5_same_set'] for r in rows)))
    with private_path(output).open('x',encoding='utf-8') as f:json.dump(report,f,ensure_ascii=False,indent=2)
    print(json.dumps(report['summary'],ensure_ascii=False))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for arg in ['baseline','candidate','output']:p.add_argument('--'+arg,type=Path,required=True)
    args=p.parse_args();main(args.baseline,args.candidate,args.output)
