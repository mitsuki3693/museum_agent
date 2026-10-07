"""Paired evidence-only experiment against immutable v1 candidates/results."""
import argparse
from collections import defaultdict
import json
import time

from app.museum.local_reranker import contextual_evidence,CONTEXT_PASSAGE_VERSION
from evaluate_local_reranker import ROOT,MODEL,MODEL_REVISION,LocalPairReranker,ranked_ids,summarize,dense_views,sha,save_new

INPUT=ROOT/'eval/private/reranker-input-v2.json'
OLD_INPUT=ROOT/'eval/private/reranker-input-v1.json'
OLD_RESULTS=ROOT/'eval/private/reranker-results-v1.json'
DIAGNOSTIC={'old-F07','old-G15','old-N02','old-T26','old-N06','old-G01','old-G05','old-G11'}


def freeze(*, input_path=INPUT, context_budget=512):
    from transformers import AutoTokenizer
    old=json.loads(OLD_INPUT.read_bytes())
    baseline_path=ROOT/'eval/private/work-fusion-v1.json'
    assert sha(baseline_path)==old['baseline_sha256']
    baseline=json.loads(baseline_path.read_bytes())
    tokenizer=AutoTokenizer.from_pretrained(str(MODEL),local_files_only=True,trust_remote_code=False)
    cases=[]
    for name,corpus in baseline['corpora'].items():
        spec=corpus['source_hashes']
        from pathlib import Path
        paths=[Path(spec['private_path']),Path(spec['public_path'])]
        assert [sha(p) for p in paths]==[spec['private_sha256'],spec['public_sha256']]
        records={r['_id']:r for p in paths for r in json.loads(p.read_bytes())}
        chunks=defaultdict(list)
        for c in dense_views(records.values())['original']:chunks[c['source_id']].append(c)
        rows={r['id']:r for r in corpus['rows']}
        for case in [c for c in old['cases'] if c['corpus']==name]:
            candidates=[]
            row=rows[case['id']]
            assert [c['source_id'] for c in row['work_evidence']]==[c['source_id'] for c in case['candidates']]
            for original,candidate in zip(case['candidates'],row['work_evidence']):
                source=records[candidate['source_id']]
                assert source['source_hash']==original['source_hash'] and source['status']=='active'
                def fits(text):
                    return len(tokenizer(case['query'],text,truncation=False)['input_ids'])<=context_budget
                evidence=contextual_evidence(source,candidate,chunks[source['_id']],fits=fits)
                candidates.append(dict(source_id=source['_id'],source_hash=source['source_hash'],**evidence))
            cases.append({**case,'candidates':candidates})
    frozen={**old,'cases':cases,'passage_version':CONTEXT_PASSAGE_VERSION,
            'context_token_budget':context_budget,
            'previous_input_sha256':sha(OLD_INPUT),'previous_results_sha256':sha(OLD_RESULTS)}
    save_new(input_path,frozen)
    print(json.dumps(dict(cases=len(cases),pairs=sum(len(c['candidates']) for c in cases),sha256=sha(input_path))))


def run(diagnostic, *, input_path=INPUT, suffix='v2'):
    import torch
    frozen=json.loads(input_path.read_bytes());assert frozen['frozen']
    assert sha(OLD_INPUT)==frozen['previous_input_sha256'] and sha(OLD_RESULTS)==frozen['previous_results_sha256']
    old=json.loads(OLD_RESULTS.read_bytes());assert old['complete']
    previous={(r['corpus'],r['id']):r for r in old['rows']}
    name=f'reranker-context-diagnostic-{suffix}' if diagnostic else f'reranker-results-{suffix}'
    out=ROOT/f'eval/private/{name}.json';journal=out.with_suffix('.jsonl')
    if out.exists() or journal.exists():raise FileExistsError('Preserve prior run')
    assert sha(MODEL/'download-manifest.json')==frozen['model_manifest_sha256']
    manifest=json.loads((MODEL/'download-manifest.json').read_bytes())
    assert manifest['revision']==MODEL_REVISION
    for file,meta in manifest['files'].items():assert sha(MODEL/file)==meta['sha256']
    settings=frozen['settings'];torch.set_num_threads(settings['threads'])
    model=LocalPairReranker(MODEL,max_length=settings['max_length'],batch_size=settings['batch_size'])
    sanity=model.score('what is panda?',['hi','The giant panda is a bear species endemic to China.'])
    assert sanity['scores'][1]>sanity['scores'][0]
    cases=[c for c in frozen['cases'] if not diagnostic or c['corpus']=='scale300' and c['id'] in DIAGNOSTIC]
    report=dict(complete=False,diagnostic=diagnostic,input_sha256=sha(input_path),model_revision=MODEL_REVISION,
        context_token_budget=frozen.get('context_token_budget',512),
        settings=settings,previous_results_sha256=sha(OLD_RESULTS),sanity=sanity,
        code_sha256={p:sha(ROOT/p) for p in ['scripts/evaluate_reranker_context.py','backend/app/museum/local_reranker.py']},rows=[])
    with journal.open('x',encoding='utf-8') as log:
        log.write(json.dumps({k:v for k,v in report.items() if k!='rows'})+'\n');log.flush()
        for i,case in enumerate(cases,1):
            start=time.perf_counter()
            result=model.score(case['query'],[c['passage'] for c in case['candidates']])
            ids=ranked_ids(case['candidates'],result['scores'])
            rank=next((j for j,s in enumerate(ids[:5],1) if s in case['gold']),None)
            prev=previous[(case['corpus'],case['id'])]
            row={k:case[k] for k in ['corpus','id','query','gold','kind','category','language','pool_contains_gold']}
            row.update(result,ids=ids,rank=rank,baseline=dict(rank=prev['rank'],ids=prev['ids'][:5]),
                       ms=(time.perf_counter()-start)*1000)
            report['rows'].append(row);log.write(json.dumps(row,ensure_ascii=False)+'\n');log.flush()
            print(json.dumps(dict(done=i,total=len(cases),corpus=case['corpus'],id=case['id'],before=prev['rank'],after=rank,ms=round(row['ms']))),flush=True)
    report.update(complete=True,summary=summarize(report['rows']))
    save_new(out,report);print(json.dumps(report['summary']),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--freeze',action='store_true');parser.add_argument('--diagnostic',action='store_true')
    parser.add_argument('--compact',action='store_true',help='New v3 input: 256-token evidence budget; model max_length stays 512')
    args=parser.parse_args()
    path=ROOT/'eval/private/reranker-input-v3.json' if args.compact else INPUT
    if args.freeze:freeze(input_path=path,context_budget=256 if args.compact else 512)
    else:run(args.diagnostic,input_path=path,suffix='v3' if args.compact else 'v2')
