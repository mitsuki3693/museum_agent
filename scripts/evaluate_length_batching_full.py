"""Full frozen ranking regression for opt-in length batching, not a benchmark."""
import json
import time
from pathlib import Path

from app.museum.local_reranker import LocalPairReranker, MODEL_REVISION, ranked_ids
from app.museum.rerank_evidence_controls import prioritize_material
from evaluate_chinese_recall import sha, rank
from evaluate_evidence_controls import load_records
from evaluate_length_batching import INPUT, BASELINE, MODEL
from evaluate_recall_rerank import request_key

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT/'eval/private/length-batching-full-v1.json'


def main():
    import torch
    journal=OUTPUT.with_suffix('.jsonl')
    if OUTPUT.exists() or journal.exists():raise FileExistsError('Preserve prior run')
    frozen=json.loads(INPUT.read_bytes());old=json.loads(BASELINE.read_bytes())
    probe_path=ROOT/'eval/private/length-batching-probe-v1.json'
    probe=json.loads(probe_path.read_bytes())
    code=ROOT/'backend/app/museum/local_reranker.py'
    assert frozen['frozen'] and old['complete'] and probe['complete'] and probe['accepted_probe']
    assert old['input_sha256']==probe['input_sha256']==sha(INPUT)
    assert probe['code_sha256']==sha(code) and probe['baseline_sha256']==sha(BASELINE)
    assert sha(ROOT/'backend/app/museum/rerank_evidence_controls.py')==frozen['module_sha256']
    manifest=json.loads((MODEL/'download-manifest.json').read_bytes())
    assert manifest['revision']==MODEL_REVISION and sha(MODEL/'download-manifest.json')==frozen['model_manifest_sha256']
    for name,info in manifest['files'].items():assert sha(MODEL/name)==info['sha256']
    records=load_records()
    previous={(r['corpus'],r['id']):r for r in old['rows']}
    by_id={c['id']:c for c in frozen['cases'] if c['corpus']=='scale300'}
    cache={request_key(by_id[r['id']]):r['runs']['length'] for r in probe['rows'] if r['round']==1}
    torch.set_num_threads(4)
    model=LocalPairReranker(MODEL,max_length=512,batch_size=4)
    report=dict(complete=False,input_sha256=sha(INPUT),baseline_sha256=sha(BASELINE),probe_sha256=sha(probe_path),
        code_sha256=sha(code),model_revision=MODEL_REVISION,api_calls=0,rows=[])
    with journal.open('x',encoding='utf-8') as log:
        for i,case in enumerate(frozen['cases'],1):
            for c in case['candidates']:assert c['source_hash']==records[case['corpus']][c['source_id']]['source_hash']
            key=request_key(case);cached=cache.get(key);start=time.perf_counter()
            result=({k:cached[k] for k in ['scores','token_lengths','truncated']} if cached else
                model.score(case['query'],[c['passage'] for c in case['candidates']],sort_by_length=True))
            assert result['truncated']==0 and max(result['token_lengths'])<=256
            ids=ranked_ids(case['candidates'],result['scores'])
            combined,_=prioritize_material(case['query'],ids,records[case['corpus']])
            prev=previous[(case['corpus'],case['id'])]
            old_ids=prev['arms']['combined']['ids']
            row={k:case[k] for k in ['corpus','id','query','gold']}
            row.update(result,request_key=key,ids=combined,rank=rank(combined[:5],case['gold']),
                baseline_rank=prev['arms']['combined']['rank'],has_baseline=prev['has_baseline'],
                top5_equal=combined[:5]==old_ids[:5],full_order_equal=combined==old_ids,
                max_score_delta=max(abs(x-y) for x,y in zip(result['scores'],prev['scores'],strict=True)),
                cached=bool(cached),ms=None if cached else round((time.perf_counter()-start)*1000))
            report['rows'].append(row);log.write(json.dumps(row,ensure_ascii=False)+'\n');log.flush()
            cache[key]=result
            print(json.dumps(dict(done=i,total=len(frozen['cases']),corpus=case['corpus'],id=case['id'],
                rank=row['rank'],top5_equal=row['top5_equal'],cached=bool(cached))),flush=True)
    report.update(complete=True,fresh_calls=sum(not r['cached'] for r in report['rows']),
        changed_top5=[(r['corpus'],r['id']) for r in report['rows'] if not r['top5_equal']],
        changed_full_order=[(r['corpus'],r['id']) for r in report['rows'] if not r['full_order_equal']],
        max_score_delta=max(r['max_score_delta'] for r in report['rows']))
    report['summary']={}
    for corpus in ['web_config','scale300']:
        rows=[r for r in report['rows'] if r['corpus']==corpus and r['has_baseline'] and r['gold']]
        report['summary'][corpus]=dict(n=len(rows),top1=sum(r['rank']==1 for r in rows),top5=sum(r['rank'] is not None for r in rows))
    with OUTPUT.open('x',encoding='utf-8') as f:json.dump(report,f,ensure_ascii=False,indent=2)
    print(json.dumps({k:report[k] for k in ['summary','fresh_calls','changed_top5','changed_full_order','max_score_delta']}),flush=True)
    print(sha(OUTPUT))


if __name__=='__main__':main()
