"""Freeze Chinese recall + original evidence, then score with local fixed BGE.

No live config, database, vision or paid API changes. Exact whole-request
cache reuse is explicit and excluded from fresh inference latency statistics.
"""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import re
import time
import statistics

from app.museum.config import MuseumSettings
from app.museum.retrieval import MuseumIndex
from app.museum.recall_fields import lexical_query
from app.museum.recall_evidence import bridge_candidate,VERSION
from app.museum.work_fusion import fuse_works
from app.museum.local_reranker import contextual_evidence,LocalPairReranker,MODEL_REVISION,ranked_ids
from app.museum.semantic_chunks import dense_views
from app.storage.store import MemoryStore
from evaluate_chinese_recall import build_fields,sha,rank

ROOT=Path(__file__).resolve().parents[1]
INPUT=ROOT/'eval/private/recall-rerank-input-v1.json'
MODEL=ROOT/'models/bge-reranker-v2-m3'
TARGETS={'M01-zh','M08-zh','A1-material-zh','old-T11','old-T27','old-T30','old-F09',
         'old-G03','old-G07','old-G13','old-F07','old-G15','old-T15','browser-samson'}


def save(path,data):
    with path.open('x',encoding='utf-8') as f:json.dump(data,f,ensure_ascii=False,indent=2)


def request_key(case):
    payload=[case['query'],[(c['source_id'],c['source_hash'],c['passage']) for c in case['candidates']]]
    return hashlib.sha256(json.dumps(payload,ensure_ascii=False).encode()).hexdigest()


async def freeze():
    import torch
    from transformers import AutoTokenizer
    torch.set_num_threads(4)
    if INPUT.exists():raise FileExistsError('Preserve frozen input')
    baseline_path=ROOT/'eval/private/chinese-recall-v3.json';baseline=json.loads(baseline_path.read_bytes())
    tokenizer=AutoTokenizer.from_pretrained(str(MODEL),local_files_only=True,trust_remote_code=False)
    cases=[]
    for name,corpus in baseline['corpora'].items():
        spec=corpus['source_hashes']
        for prefix in ['public','private']:assert sha(Path(spec[prefix+'_path']))==spec[prefix+'_sha256']
        index=MuseumIndex(MuseumSettings(_env_file=None,museum_corpus=Path(spec['public_path']),
            museum_private_corpus=Path(spec['private_path']),museum_embedding='local',museum_text_rerank=True,
            deepseek_api_key=''),MemoryStore())
        await index.start()
        manifest_path=ROOT/f'data/private/chinese-recall-v2-{name}.json'
        assert sha(manifest_path)==corpus['manifest_sha256']
        manifest=json.loads(manifest_path.read_bytes());annotations={e['source_id']:e for e in manifest['records']}
        field_index,_=build_fields(manifest,index.records)
        for row in corpus['rows']:
            query=row['query'];vector=index.embedding._local_embed([query])[0]
            lexical=field_index.search(lexical_query(query),top_k=25) if re.search('[\u4e00-\u9fff]',query) else index.bm25.search(query,top_k=25)
            pool=fuse_works(lexical,await index.rerank_vectors.search(vector,top_k=25))
            assert [c['source_id'] for c in pool]==row['arms']['extended_fields']['pool_ids'],(name,row['id'],'pool drift')
            candidates=[]
            for candidate in pool:
                source=index.records[candidate['source_id']]
                bridged,audit=bridge_candidate(query,candidate,source,annotations.get(source['_id']))
                def fits(text):return len(tokenizer(query,text,truncation=False)['input_ids'])<=256
                evidence=contextual_evidence(source,bridged,dense_views([source])['original'],fits=fits)
                candidates.append(dict(source_id=source['_id'],source_hash=source['source_hash'],bridge=audit,**evidence))
            case=dict(corpus=name,**{k:row[k] for k in ['id','query','gold','kind','category','language']},candidates=candidates)
            case['request_key']=request_key(case);cases.append(case)
        print(json.dumps(dict(frozen_corpus=name,rows=len(corpus['rows']))),flush=True)
    data=dict(frozen=True,baseline_sha256=sha(baseline_path),bridge_version=VERSION,context_token_budget=256,
              model_manifest_sha256=sha(MODEL/'download-manifest.json'),model_revision=MODEL_REVISION,
              settings=dict(max_length=512,batch_size=4,threads=4),cases=cases)
    save(INPUT,data);print(json.dumps(dict(frozen=len(cases),pairs=sum(len(c['candidates']) for c in cases),sha256=sha(INPUT))))


def run(full):
    import torch
    frozen=json.loads(INPUT.read_bytes());assert frozen['frozen']
    manifest=json.loads((MODEL/'download-manifest.json').read_bytes())
    assert sha(MODEL/'download-manifest.json')==frozen['model_manifest_sha256'] and manifest['revision']==MODEL_REVISION
    for name,meta in manifest['files'].items():assert sha(MODEL/name)==meta['sha256']
    prior_path=ROOT/'eval/private/reranker-results-v3.json';prior=json.loads(prior_path.read_bytes())
    old_input_path=ROOT/'eval/private/reranker-input-v3.json';old_input=json.loads(old_input_path.read_bytes())
    assert prior['complete'] and old_input['frozen']
    assert prior['input_sha256']==sha(old_input_path) and prior['model_revision']==MODEL_REVISION
    assert old_input['model_manifest_sha256']==frozen['model_manifest_sha256']
    assert all(old_input['settings'][k]==v for k,v in frozen['settings'].items())
    previous={(r['corpus'],r['id']):r for r in prior['rows']}
    cache={request_key(c):previous[(c['corpus'],c['id'])] for c in old_input['cases']}
    diagnostic=ROOT/'eval/private/recall-rerank-diagnostic-v1.json'
    if full and diagnostic.exists():
        diag=json.loads(diagnostic.read_bytes());assert diag['input_sha256']==sha(INPUT)
        cache.update({r['request_key']:r for r in diag['rows']})
    output=ROOT/('eval/private/recall-rerank-results-v1.json' if full else 'eval/private/recall-rerank-diagnostic-v1.json')
    journal=output.with_suffix('.jsonl')
    if output.exists() or journal.exists():raise FileExistsError('Preserve prior run')
    torch.set_num_threads(4);model=LocalPairReranker(MODEL,max_length=512,batch_size=4)
    cases=[c for c in frozen['cases'] if full or c['corpus']=='scale300' and c['id'] in TARGETS]
    report=dict(complete=False,scope='full' if full else 'targeted',input_sha256=sha(INPUT),
                previous_results_sha256=sha(prior_path),model_revision=MODEL_REVISION,api_calls=0,rows=[])
    with journal.open('x',encoding='utf-8') as log:
        for i,case in enumerate(cases,1):
            start=time.perf_counter();cached=cache.get(case['request_key']) if full else None
            result=({k:cached[k] for k in ['scores','token_lengths','truncated']} if cached else
                    model.score(case['query'],[c['passage'] for c in case['candidates']]))
            assert result['truncated']==0 and max(result['token_lengths'])<=256
            ordered=ranked_ids(case['candidates'],result['scores'])
            prev=previous.get((case['corpus'],case['id']))
            row={k:case[k] for k in ['corpus','id','query','gold','kind','request_key']}
            row.update(result,ids=ordered,rank=rank(ordered[:5],case['gold']),
                       baseline_rank=prev['rank'] if prev else None,has_baseline=bool(prev),
                       cached=bool(cached),ms=None if cached else round((time.perf_counter()-start)*1000))
            report['rows'].append(row);log.write(json.dumps(row,ensure_ascii=False)+'\n');log.flush()
            print(json.dumps(dict(done=i,total=len(cases),corpus=case['corpus'],id=case['id'],before=row['baseline_rank'],
                                  after=row['rank'],cached=row['cached'],ms=row['ms'])),flush=True)
    report['summary']={}
    for name in {r['corpus'] for r in report['rows']}:
        rows=[r for r in report['rows'] if r['corpus']==name and r['has_baseline'] and r['gold']]
        report['summary'][name]=dict(n=len(rows),before_top5=sum(r['baseline_rank'] is not None for r in rows),
            after_top5=sum(r['rank'] is not None for r in rows),before_top1=sum(r['baseline_rank']==1 for r in rows),
            after_top1=sum(r['rank']==1 for r in rows),
            regressed_top5=[r['id'] for r in rows if r['baseline_rank'] is not None and r['rank'] is None],
            regressed_top1=[r['id'] for r in rows if r['baseline_rank']==1 and r['rank']!=1])
    fresh=[r['ms'] for r in report['rows'] if not r['cached']]
    report.update(complete=True,fresh_calls=len(fresh),cached_calls=len(report['rows'])-len(fresh),
                  fresh_p50_ms=statistics.median(fresh) if fresh else None)
    save(output,report);print(json.dumps(dict(summary=report['summary'],sha256=sha(output))),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--freeze',action='store_true');p.add_argument('--full',action='store_true');args=p.parse_args()
    if args.freeze:asyncio.run(freeze())
    else:run(args.full)
