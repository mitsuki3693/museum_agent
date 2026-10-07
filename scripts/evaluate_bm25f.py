"""Offline text-only ablation; source data and reports stay in private directories.

No model downloads, external API calls, image changes, live configuration edits,
or implied human review. Lexical arms deliberately do NOT change live RRF.
"""
import argparse
import asyncio
import ctypes
import hashlib
import json
import math
from pathlib import Path
import statistics
import sys
import time

from app.museum.config import MuseumSettings
from app.museum.retrieval import MuseumIndex
from app.museum.search_fields import FIELD_WEIGHTS, field_documents, flattened_documents, load_search_fields
from app.retrieval.bm25 import BM25Index
from app.retrieval.bm25f import BM25FIndex
from app.storage.store import MemoryStore

ROOT = Path(__file__).resolve().parents[1]


def working_set():
    """Windows current process working set; zero external dependencies."""
    if sys.platform != 'win32':
        return None
    class Counters(ctypes.Structure):
        _fields_=[('cb',ctypes.c_ulong),('faults',ctypes.c_ulong)]+[
            (n,ctypes.c_size_t) for n in ('peak','working','peak_paged','paged','peak_nonpaged','nonpaged','pagefile','peak_pagefile')]
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    api=ctypes.WinDLL('psapi',use_last_error=True)
    kernel.GetCurrentProcess.restype=ctypes.c_void_p
    api.GetProcessMemoryInfo.argtypes=[ctypes.c_void_p,ctypes.POINTER(Counters),ctypes.c_ulong]
    data=Counters();data.cb=ctypes.sizeof(data)
    if not api.GetProcessMemoryInfo(kernel.GetCurrentProcess(),ctypes.byref(data),data.cb):
        raise ctypes.WinError(ctypes.get_last_error())
    return data.working


def memory_delta(before):
    after=working_set()
    return after-before if before is not None and after is not None else None


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def stats(rows):
    targeted=[r for r in rows if r['gold']]
    if not targeted:
        return {}
    scores={}
    for name in rows[0]['arms']:
        values=[r['arms'][name] for r in targeted]
        times=sorted(v['ms'] for v in values)
        scores[name]=dict(n=len(values),top1=sum(v['rank']==1 for v in values),
            top5=sum(v['rank'] is not None and v['rank']<=5 for v in values),
            top25=None if name=='current_hybrid' else sum(v['rank'] is not None and v['rank']<=25 for v in values),
            mrr_at5=sum(1/v['rank'] if v['rank'] and v['rank']<=5 else 0 for v in values)/len(values),
            returned_limit=5 if name=='current_hybrid' else 25,
            p50_ms=statistics.median(times),p95_ms=times[math.ceil(len(times)*.95)-1])
    return scores


def stage_ranks(hits, gold, limit=25):
    """Differentiate a chunk cutoff from deduplicated work recall."""
    works=list(dict.fromkeys(h['source_id'] for h in hits))
    return dict(best_chunk_rank=next((i for i,h in enumerate(hits,1) if h['source_id'] in gold),None),
                work_rank=next((i for i,sid in enumerate(works,1) if sid in gold),None),
                coarse_contains_gold=any(h['source_id'] in gold for h in hits[:limit]),
                coarse_unique_works=len({h['source_id'] for h in hits[:limit]}))


def expectation_coverage(ids, expected):
    """Known relevant examples, NOT an exhaustive relevance label or accuracy."""
    expected=list(dict.fromkeys(expected))
    present=[sid for sid in expected if sid in ids[:5]]
    return dict(known_examples=expected,present_top5=present,
                count=len(present),total=len(expected))


async def main():
    import torch  # Optional local-model dependency; metric helpers need no torch.
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,default=ROOT/'eval/private/bm25f-v1.json')
    parser.add_argument('--manifest',type=Path,default=ROOT/'data/private/va-search-fields-v1.json')
    parser.add_argument('--queries',type=Path,action='append',help='Repeat to override default frozen query sets')
    parser.add_argument('--diagnose',action='store_true',help='Record full original BM25/dense ranks, without changing ranking')
    args=parser.parse_args()
    if args.output.exists():raise FileExistsError('Preserve completed evaluation evidence')
    torch.set_num_threads(4)
    corpus=ROOT/'data/private/va-pilot-300-v1-corpus.json'
    manifest=args.manifest
    baseline_path=ROOT/'eval/private/scale-300-v1.json'
    baseline=json.loads(baseline_path.read_bytes())
    assert baseline['complete'] and sha(corpus)==baseline['versions']['300']['corpus_sha256']
    sets=args.queries or [ROOT/'eval/private'/n for n in ['scale-300-text-v1.json','scale-300-new-text-v1.json','bm25f-new-queries-v1.json']]
    seen=set()
    for path in sets:
        qset=json.loads(path.read_bytes())
        assert qset['frozen']
        frozen=qset.get('frozen_versions',{})
        if frozen:
            assert frozen['corpus_sha256']==sha(corpus)
            assert frozen['fields_sha256']==sha(manifest)
        for c in qset['cases']:
            if c['id'] in seen:raise ValueError('Duplicate query ID')
            seen.add(c['id'])
    cfg=MuseumSettings(_env_file=None,museum_private_corpus=corpus,museum_embedding='local',deepseek_api_key='')
    assert cfg.museum_embedding_model==baseline['embedding_model'], 'Baseline encoder changed'
    index=MuseumIndex(cfg,MemoryStore())
    rss=working_set();t=time.perf_counter();await index.start()
    report=dict(complete=False,human_reviewed=False,
        scope='Offline candidate retrieval, not generated answers or user accuracy. Agent-authored queries; no human review.',
        versions=dict(corpus=sha(corpus),public_corpus=sha(cfg.museum_corpus),baseline=sha(baseline_path),
                      queries={p.name:sha(p) for p in sets},model=cfg.museum_embedding_model,
                      code={p:sha(ROOT/p) for p in ['scripts/evaluate_bm25f.py','backend/app/museum/search_fields.py','backend/app/retrieval/bm25f.py']}),
        fixed=dict(weights=FIELD_WEIGHTS,k1=1.5,b=.75,lexical_top=25,production_top=5,threads=4),
        build=dict(baseline_ms=(time.perf_counter()-t)*1000,baseline_working_set_delta=memory_delta(rss)),rows=[])
    fields,meta=load_search_fields(manifest,index.records,allow_drafts=True)
    report['versions']['fields']=meta
    arms={}
    for name,docs in [('work_bm25',field_documents(index.records)),('enriched_bm25',field_documents(index.records,fields)),('bm25f',field_documents(index.records,fields))]:
        before=working_set();t=time.perf_counter()
        obj=BM25FIndex(FIELD_WEIGHTS) if name=='bm25f' else BM25Index()
        obj.index(docs if name=='bm25f' else flattened_documents(docs))
        report['build'][name]=dict(ms=(time.perf_counter()-t)*1000,working_set_delta=memory_delta(before))
        arms[name]=obj
    old_ids={r['id']:r['results']['300']['ids'] for r in baseline['text']}
    old_ids.update({r['id']:r['ids'] for r in baseline['new_text']})
    for path in sets:
        qset=json.loads(path.read_bytes());assert qset['frozen']
        for case in qset['cases']:
            for sid in case['gold']:
                if case.get('source_hash'):assert index.records[sid]['source_hash']==case['source_hash']
                assert case['evidence_anchor'] in index.records[sid]['content']
            row={k:case[k] for k in ('id','query','category','gold')};row['arms']={}
            t=time.perf_counter();hits=await index.search(case['query']);elapsed=(time.perf_counter()-t)*1000
            ids=[h['_id'] for h in hits]
            if case['id'] in old_ids:assert ids==old_ids[case['id']],('Baseline drift',case['id'])
            variants=[('current_hybrid',ids,elapsed,None)]
            for name,obj in arms.items():
                t=time.perf_counter();hits=obj.search(case['query'],top_k=25);elapsed=(time.perf_counter()-t)*1000
                variants.append((name,[h['source_id'] for h in hits],elapsed,hits if name=='bm25f' else None))
            for name,ids,elapsed,evidence in variants:
                rank=next((i+1 for i,sid in enumerate(ids) if sid in case['gold']),None)
                row['arms'][name]=dict(ids=ids,rank=rank,ms=elapsed)
                if case.get('known_relevant_examples'):
                    row['arms'][name]['example_coverage']=expectation_coverage(ids,case['known_relevant_examples'])
                if evidence is not None:row['arms'][name]['matched_fields']=[dict(id=h['id'],fields=h['matched_fields']) for h in evidence]
            if args.diagnose and case['gold']:
                vector=(await asyncio.to_thread(index.embedding._local_embed,[case['query']]))[0]
                count=await index.vectors.count()
                bm=index.bm25.search(case['query'],top_k=count)
                dense=await index.vectors.search(vector,top_k=count)
                row['stages']={name:stage_ranks(hits,case['gold']) for name,hits in [('original_bm25',bm),('original_dense',dense)]}
            report['rows'].append(row)
    report['summary']=stats(report['rows'])
    report['by_category']={c:stats([r for r in report['rows'] if r['category']==c]) for c in sorted({r['category'] for r in report['rows'] if r['gold']})}
    report['changes']={}
    for a,b in [('current_hybrid','bm25f'),('work_bm25','enriched_bm25'),('enriched_bm25','bm25f')]:
        ok=lambda r,n:r['arms'][n]['rank'] is not None and r['arms'][n]['rank']<=5
        report['changes'][a+'->'+b]=dict(fixed=[r['id'] for r in report['rows'] if r['gold'] and not ok(r,a) and ok(r,b)],
            regressed=[r['id'] for r in report['rows'] if r['gold'] and ok(r,a) and not ok(r,b)])
    report['complete']=True
    with args.output.open('x',encoding='utf-8') as f:json.dump(report,f,ensure_ascii=False,indent=2)
    print(json.dumps(dict(summary=report['summary'],changes=report['changes']),ensure_ascii=True),flush=True)


if __name__=='__main__':asyncio.run(main())
