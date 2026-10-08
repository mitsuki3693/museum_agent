"""Local paired performance probe; all candidates and evidence stay frozen.

Six fixed questions, three AB/BA rounds, one warm-up for each arm, no cache
reuse for timings. Default application scoring remains in original order.
"""
import json
import math
import statistics
import time
from pathlib import Path

from app.museum.local_reranker import LocalPairReranker, MODEL_REVISION, ranked_ids, batch_indices
from app.museum.rerank_evidence_controls import prioritize_material
from evaluate_evidence_controls import load_records
from evaluate_chinese_recall import sha, rank

ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT/'eval/private/evidence-controls-input-v1.json'
BASELINE = ROOT/'eval/private/evidence-controls-results-v1.json'
MODEL = ROOT/'models/bge-reranker-v2-m3'
OUTPUT = ROOT/'eval/private/length-batching-probe-v1.json'
IDS = ['M05-zh', 'old-G01', 'old-T30', 'old-T11', 'old-T26', 'A1-material-en']


def main():
    import torch
    if OUTPUT.exists() or OUTPUT.with_suffix('.jsonl').exists():
        raise FileExistsError('Preserve prior benchmark')
    frozen = json.loads(INPUT.read_bytes()); baseline = json.loads(BASELINE.read_bytes())
    assert frozen['frozen'] and baseline['complete'] and baseline['input_sha256']==sha(INPUT)
    assert sha(ROOT/'backend/app/museum/rerank_evidence_controls.py')==frozen['module_sha256']
    manifest = json.loads((MODEL/'download-manifest.json').read_bytes())
    assert manifest['revision']==MODEL_REVISION==frozen['model_revision']
    assert sha(MODEL/'download-manifest.json')==frozen['model_manifest_sha256']
    for name, info in manifest['files'].items():assert sha(MODEL/name)==info['sha256']
    records = load_records()['scale300']
    cases = [next(c for c in frozen['cases'] if c['corpus']=='scale300' and c['id']==cid) for cid in IDS]
    for case in cases:
        for c in case['candidates']:assert c['source_hash']==records[c['source_id']]['source_hash']
    previous = {r['id']:r for r in baseline['rows'] if r['corpus']=='scale300'}
    torch.set_num_threads(4)
    model = LocalPairReranker(MODEL,max_length=512,batch_size=4)
    report = dict(complete=False, input_sha256=sha(INPUT), baseline_sha256=sha(BASELINE),
        code_sha256=sha(ROOT/'backend/app/museum/local_reranker.py'), model_revision=MODEL_REVISION,
        torch_version=torch.__version__, threads=4, batch_size=4, dtype='float32', device='cpu',
        api_calls=0, cases=IDS, rounds=3, warmup_calls=2, acceptance='median paired ratio <=0.90, unchanged combined Top5 order', rows=[])
    warmup = cases[0]
    for arm in [False,True]:
        model.score(warmup['query'],[c['passage'] for c in warmup['candidates']],sort_by_length=arm)
    with OUTPUT.with_suffix('.jsonl').open('x',encoding='utf-8') as log:
        for repetition in range(3):
            for number, case in enumerate(cases):
                runs = {}
                order = ['original','length'] if (repetition+number)%2==0 else ['length','original']
                for arm in order:
                    start = time.perf_counter()
                    result = model.score(case['query'],[c['passage'] for c in case['candidates']],sort_by_length=arm=='length')
                    ms = (time.perf_counter()-start)*1000
                    assert result['truncated']==0 and max(result['token_lengths'])<=256
                    ordered = ranked_ids(case['candidates'],result['scores'])
                    combined,_ = prioritize_material(case['query'],ordered,records)
                    lengths=result['token_lengths']
                    padded=sum(len(group)*max(lengths[i] for i in group) for group in
                        batch_indices(lengths,4,sort_by_length=arm=='length'))
                    runs[arm] = dict(**result,ms=round(ms,3),ids=ordered,combined_ids=combined,
                        rank=rank(combined[:5],case['gold']),padded_tokens=padded,
                        baseline_top5_equal=combined[:5]==previous[case['id']]['arms']['combined']['ids'][:5])
                a,b=runs['original'],runs['length']
                row=dict(id=case['id'],round=repetition+1,order=order,runs=runs,
                    ratio=b['ms']/a['ms'],max_score_delta=max(abs(x-y) for x,y in zip(a['scores'],b['scores'],strict=True)),
                    top5_equal=a['combined_ids'][:5]==b['combined_ids'][:5],
                    full_order_equal=a['combined_ids']==b['combined_ids'])
                report['rows'].append(row);log.write(json.dumps(row)+'\n');log.flush()
                print(json.dumps(dict(done=len(report['rows']),total=18,id=case['id'],round=repetition+1,
                    original_ms=round(a['ms']),length_ms=round(b['ms']),ratio=round(row['ratio'],3),
                    top5_equal=row['top5_equal'],score_delta=row['max_score_delta'])),flush=True)
    rows=report['rows']
    report['summary'] = dict(paired_median_ratio=statistics.median(r['ratio'] for r in rows),
        changed_top5=sum(not r['top5_equal'] for r in rows),changed_full_order=sum(not r['full_order_equal'] for r in rows),
        baseline_top5_drift=sum(not arm['baseline_top5_equal'] for r in rows for arm in r['runs'].values()),
        max_score_delta=max(r['max_score_delta'] for r in rows),
        by_case={cid:dict(median_ratio=statistics.median(r['ratio'] for r in rows if r['id']==cid)) for cid in IDS})
    for arm in ['original','length']:
        durations=[r['runs'][arm]['ms'] for r in rows]
        report['summary'][arm]=dict(p50_ms=statistics.median(durations),p95_ms=sorted(durations)[math.ceil(.95*len(durations))-1],
            over_8sec=sum(t>8000 for t in durations))
    report.update(complete=True,measured_calls=36,accepted_probe=report['summary']['paired_median_ratio']<=.90
                  and report['summary']['changed_top5']==0 and report['summary']['baseline_top5_drift']==0)
    with OUTPUT.open('x',encoding='utf-8') as f:json.dump(report,f,ensure_ascii=False,indent=2)
    print(json.dumps(dict(summary=report['summary'],accepted=report['accepted_probe'],sha256=sha(OUTPUT))),flush=True)


if __name__=='__main__':main()
