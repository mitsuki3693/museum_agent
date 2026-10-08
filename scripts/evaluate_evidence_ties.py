"""Frozen-score replay; --probe separately runs six fresh local model calls."""
import argparse
import json
from pathlib import Path

from app.museum.evidence_ties import POLICY, stable_evidence_ranking
from app.museum.local_reranker import LocalPairReranker
from app.museum.rerank_evidence_controls import prioritize_material
from evaluate_chinese_recall import sha, rank
from evaluate_evidence_controls import load_records
from evaluate_recall_rerank import request_key

ROOT=Path(__file__).resolve().parents[1]
PRIVATE=ROOT/'eval/private'
FILES={
    'evidence-controls-input-v1.json':'f9007b87fa6a5c6635d566434bdf6cf05eeb4c3f84fe7cb824d88c9b48418d20',
    'evidence-controls-results-v1.json':'1d1cb101f4c0331a4115b531a4cf0f789b76c018b20886a10bf3b12dba300b35',
    'length-batching-full-v1.json':'37a41ac070e0238a513644e4c7b2049223f5d2df23e9df854bcf7917f277ca06'}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--probe',action='store_true');args=parser.parse_args()
    out=PRIVATE/('evidence-ties-probe-v1.json' if args.probe else 'evidence-ties-replay-v1.json')
    if out.exists():raise FileExistsError('Preserve prior result')
    for name,digest in FILES.items():assert sha(PRIVATE/name)==digest
    frozen,old,new=[json.loads((PRIVATE/name).read_bytes()) for name in FILES]
    assert old['complete'] and new['complete'] and frozen['frozen']
    before={(r['corpus'],r['id']):r for r in old['rows']}
    after={(r['corpus'],r['id']):r for r in new['rows']}
    records=load_records()
    report=dict(complete=False,policy=POLICY,module_sha256=sha(ROOT/'backend/app/museum/evidence_ties.py'),
                inputs=FILES,api_calls=0,new_model_calls=0,rows=[])
    model=None
    if args.probe:
        import torch
        torch.set_num_threads(4)
        path=ROOT/'models/bge-reranker-v2-m3'
        manifest=json.loads((path/'download-manifest.json').read_bytes())
        assert sha(path/'download-manifest.json')==frozen['model_manifest_sha256']
        for name,meta in manifest['files'].items():assert sha(path/name)==meta['sha256']
        model=LocalPairReranker(path,max_length=512,batch_size=4)
    for case in frozen['cases']:
        key=(case['corpus'],case['id']);prior=before[key];latest=after[key]
        if args.probe and (key[0]!='scale300' or key[1] not in ['M04-zh','old-G17','old-G18']):continue
        assert latest['request_key']==request_key(case)
        cs=case['candidates'];passages=[c['passage'] for c in cs]
        for c in cs:assert c['source_hash']==records[key[0]][c['source_id']]['source_hash']
        arms={}
        for name,cached,sorted_batch in [('original',prior,False),('length',latest,True)]:
            if model:
                result=model.score(case['query'],passages,sort_by_length=sorted_batch)
                assert result['truncated']==0 and max(result['token_lengths'])<=256
                scores=result['scores'];report['new_model_calls']+=1
            else:scores=cached['scores']
            ids,ties=stable_evidence_ranking(cs,scores,passages)
            ids,_=prioritize_material(case['query'],ids,records[key[0]])
            arms[name]=dict(ids=ids,ties=ties,scores=scores,rank=rank(ids[:5],case['gold']))
        row=dict(corpus=key[0],id=key[1],arms=arms,has_baseline=latest['has_baseline'],
                 baseline_rank=prior['arms']['combined']['rank'],gold=case['gold'],
                 full_order_equal=arms['original']['ids']==arms['length']['ids'],
                 top5_set_unchanged=set(arms['length']['ids'][:5])==set(prior['arms']['combined']['ids'][:5]))
        report['rows'].append(row)
        if model:print(json.dumps(dict(id=key[1],full_order_equal=row['full_order_equal'])),flush=True)
    report.update(complete=True,changed_order=[(r['corpus'],r['id']) for r in report['rows'] if not r['full_order_equal']],
                  changed_top5_set=[(r['corpus'],r['id']) for r in report['rows'] if not r['top5_set_unchanged']])
    report['summary']={}
    for corpus in ['web_config','scale300']:
        rows=[r for r in report['rows'] if r['corpus']==corpus and r['has_baseline'] and r['gold']]
        report['summary'][corpus]=dict(n=len(rows),top1=sum(r['arms']['length']['rank']==1 for r in rows),
            top5=sum(r['arms']['length']['rank'] is not None for r in rows))
    with out.open('x',encoding='utf-8') as f:json.dump(report,f,ensure_ascii=False,indent=2)
    print(json.dumps({k:report[k] for k in ['new_model_calls','changed_order','changed_top5_set','summary']}),flush=True)
    print(sha(out))
    assert not report['changed_order'] and not report['changed_top5_set']


if __name__=='__main__':main()
