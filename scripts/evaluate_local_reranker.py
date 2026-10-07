"""Freeze existing work candidates, then locally rerank without new retrieval."""
import argparse
import json
from pathlib import Path
import statistics
import time

from app.museum.local_reranker import evidence_passage,ranked_ids,LocalPairReranker,MODEL_REVISION,PASSAGE_VERSION
from app.museum.semantic_chunks import dense_views
from evaluate_metadata_queries import sha,save_new

ROOT=Path(__file__).resolve().parents[1]
INPUT=ROOT/'eval/private/reranker-input-v1.json'
OUT=ROOT/'eval/private/reranker-results-v1.json'
JOURNAL=ROOT/'eval/private/reranker-progress-v1.jsonl'
MODEL=ROOT/'models/bge-reranker-v2-m3'
TARGETS={'old-T26','old-N06','old-G01','old-G05','old-G11'}


def freeze():
    baseline_path=ROOT/'eval/private/work-fusion-v1.json'
    baseline=json.loads(baseline_path.read_bytes());assert baseline['complete']
    cases=[]
    for name,corpus in baseline['corpora'].items():
        spec=corpus['source_hashes'];private,public=Path(spec['private_path']),Path(spec['public_path'])
        assert sha(private)==spec['private_sha256'] and sha(public)==spec['public_sha256']
        records={r['_id']:r for r in json.loads(private.read_bytes())+json.loads(public.read_bytes())}
        chunks={c['_id']:c for c in dense_views(records.values())['original']}
        for row in corpus['rows']:
            candidates=[]
            for candidate in row['work_evidence']:
                source=records[candidate['source_id']];assert source['status']=='active'
                for evidence in candidate['lanes'].values():
                    chunk=chunks[evidence['best_chunk_id']]
                    assert chunk['source_id']==source['_id'] and chunk['content']==evidence['best_content']
                passage=evidence_passage(source,candidate)
                candidates.append(dict(source_id=source['_id'],passage=passage,source_hash=source['source_hash']))
            assert [c['source_id'] for c in candidates[:5]]==row['results']['work']['ids']
            cases.append(dict(corpus=name,**{k:row[k] for k in ['id','query','gold','kind','category','language']},
                baseline=row['results']['work'],candidates=candidates,
                pool_contains_gold=bool(set(row['gold'])&{c['source_id'] for c in candidates})))
    manifest=json.loads((MODEL/'download-manifest.json').read_bytes());assert manifest['revision']==MODEL_REVISION
    save_new(INPUT,dict(frozen=True,human_reviewed=False,baseline_sha256=sha(baseline_path),
        passage_version=PASSAGE_VERSION,model_manifest_sha256=sha(MODEL/'download-manifest.json'),
        settings=dict(max_length=512,batch_size=4,dtype='float32',device='cpu',threads=4),cases=cases))
    print(json.dumps(dict(frozen=len(cases),pairs=sum(len(c['candidates']) for c in cases),input_sha256=sha(INPUT))))


def summarize(rows):
    summary={}
    for name in sorted({r['corpus'] for r in rows}):
        selected=[r for r in rows if r['corpus']==name]
        groups={}
        for kind in ['facet','named','old']:
            items=[r for r in selected if r['kind']==kind]
            groups[kind]=dict(n=len(items),baseline_top1=sum(r['baseline']['rank']==1 for r in items),
                baseline_top5=sum(r['baseline']['rank'] is not None for r in items),
                rerank_top1=sum(r['rank']==1 for r in items),rerank_top5=sum(r['rank'] is not None for r in items),
                pool_contains_gold=sum(r['pool_contains_gold'] for r in items))
        changes={}
        for cutoff in [1,5]:
            def ok(rank):return rank is not None and rank<=cutoff
            changes[f'top{cutoff}']=dict(fixed=[r['id'] for r in selected if not ok(r['baseline']['rank']) and ok(r['rank'])],
                regressed=[r['id'] for r in selected if ok(r['baseline']['rank']) and not ok(r['rank'])])
        times=sorted(r['ms'] for r in selected)
        summary[name]=dict(groups=groups,changes=changes,p50_rerank_ms=statistics.median(times),
            p95_rerank_ms=times[(95*len(times)+99)//100-1],truncated_pairs=sum(r['truncated'] for r in selected))
    return summary


def run():
    import torch
    torch.set_num_threads(4)
    if OUT.exists() or JOURNAL.exists():raise FileExistsError('Preserve completed or interrupted run')
    frozen=json.loads(INPUT.read_bytes());assert frozen['frozen']
    assert sha(MODEL/'download-manifest.json')==frozen['model_manifest_sha256']
    manifest=json.loads((MODEL/'download-manifest.json').read_bytes());assert manifest['revision']==MODEL_REVISION
    for name,meta in manifest['files'].items():assert sha(MODEL/name)==meta['sha256']
    settings=frozen['settings']
    reranker=LocalPairReranker(MODEL,max_length=settings['max_length'],batch_size=settings['batch_size'])
    sanity=reranker.score('what is panda?',['hi','The giant panda is a bear species endemic to China.'])
    assert sanity['scores'][1]>sanity['scores'][0], 'Model sanity check failed'
    report=dict(complete=False,input_sha256=sha(INPUT),model_revision=MODEL_REVISION,settings=settings,
        code_sha256={p:sha(ROOT/p) for p in ['scripts/evaluate_local_reranker.py','backend/app/museum/local_reranker.py']},sanity=sanity,rows=[])
    # Run the five target failures first, but always evaluate the entire frozen set.
    cases=sorted(frozen['cases'],key=lambda c:not(c['corpus']=='scale300' and c['id'] in TARGETS))
    with JOURNAL.open('x',encoding='utf-8') as log:
        log.write(json.dumps(dict(event='manifest',**{k:v for k,v in report.items() if k!='rows'}))+'\n');log.flush()
        for i,case in enumerate(cases,1):
            start=time.perf_counter()
            result=reranker.score(case['query'],[c['passage'] for c in case['candidates']])
            ids=ranked_ids(case['candidates'],result['scores'])
            row={k:case[k] for k in ['corpus','id','query','gold','kind','category','language','baseline','pool_contains_gold']}
            row.update(result,ids=ids,rank=next((j for j,s in enumerate(ids[:5],1) if s in case['gold']),None),
                       ms=(time.perf_counter()-start)*1000)
            report['rows'].append(row);log.write(json.dumps(row,ensure_ascii=False)+'\n');log.flush()
            print(json.dumps(dict(done=i,total=len(cases),corpus=case['corpus'],id=case['id'],
                before=case['baseline']['rank'],after=row['rank'],ms=round(row['ms']))),flush=True)
    report['summary']=summarize(report['rows']);report['complete']=True
    save_new(OUT,report)
    print(json.dumps(report['summary']),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--freeze',action='store_true')
    args=parser.parse_args()
    if args.freeze:freeze()
    else:run()
