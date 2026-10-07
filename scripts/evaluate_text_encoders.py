"""Paired local MiniLM/E5 experiment with unchanged chunk BM25 and RRF.

All 72 targeted + 8 ambiguous queries were frozen before this experiment.
Chinese auxiliary fields are fingerprinted but NOT added to either arm: the
existing live pipeline does not use those experimental fields yet.
"""
import asyncio
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import statistics
import time

from app.museum.config import MuseumSettings
from app.museum.retrieval import MuseumIndex, FusionOrder
from app.museum.text_encoder import E5, MINILM, RetrievalEncoder
from app.retrieval.hybrid import HybridRetriever
from app.retrieval.vector_store import MemoryVectorStore
from app.storage.store import MemoryStore
from evaluate_bm25f import stage_ranks, expectation_coverage, working_set, memory_delta

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'eval/private/text-encoders-v1.json'


def sha(path):
    with path.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()


def summarize(rows,name):
    values=[r['results'][name] for r in rows if r['gold']]
    if not values:return {}
    times=sorted(r['ms'] for r in values)
    return dict(n=len(values),top1=sum(v['rank']==1 for v in values),
        top5=sum(v['rank'] is not None for v in values),
        mrr_at5=sum(1/v['rank'] if v['rank'] else 0 for v in values)/len(values),
        dense_recall25=sum(v['dense']['coarse_contains_gold'] for v in values),
        p50_ms=statistics.median(times),p95_ms=times[(95*len(times)+99)//100-1])


def truncation(encoder,texts,is_query):
    prepared=encoder.inputs(texts,is_query=is_query)
    lengths=[len(ids) for ids in encoder.model.tokenizer(prepared,truncation=False)['input_ids']]
    return dict(max_seq_length=encoder.model.max_seq_length,max_input_tokens=max(lengths),
        truncated=sum(n>encoder.model.max_seq_length for n in lengths))


async def main():
    import torch
    from sentence_transformers import SentenceTransformer
    if OUT.exists():raise FileExistsError('Preserve completed or interrupted evidence')
    torch.set_num_threads(4)
    corpus=ROOT/'data/private/va-pilot-300-v1-corpus.json'
    fields=ROOT/'data/private/va-search-fields-v1.1.json'
    query_names=['scale-300-text-v1.json','scale-300-new-text-v1.json','bm25f-new-queries-v1.json','text-generalization-v1-queries.json']
    sets=[ROOT/'eval/private'/n for n in query_names]
    cases=[]
    for path in sets:
        doc=json.loads(path.read_bytes());assert doc['frozen']
        if doc.get('frozen_versions'):
            assert doc['frozen_versions']==dict(corpus_sha256=sha(corpus),fields_sha256=sha(fields))
        cases+=doc['cases']
    assert len({c['id'] for c in cases})==80 and sum(bool(c['gold']) for c in cases)==72
    previous={}
    for name in ['bm25f-v1.1.json','text-generalization-v1.json']:
        p=json.loads((ROOT/'eval/private'/name).read_bytes());assert p['complete']
        assert p['versions']['corpus']==sha(corpus)
        previous.update({r['id']:r['arms']['current_hybrid']['ids'] for r in p['rows']})
    e5path=ROOT/'models/multilingual-e5-base'
    downloaded=json.loads((e5path/'download-manifest.json').read_bytes())
    assert downloaded['revision']==E5.revision
    for name,meta in downloaded['files'].items():assert sha(e5path/name)==meta['sha256']
    minipath=ROOT/'models/models--sentence-transformers--paraphrase-multilingual-MiniLM-L12-v2/snapshots'/MINILM.revision
    assert minipath.exists()
    report=dict(complete=False,human_reviewed=False,scope='Offline encoder-only comparison; no generated answers, visual changes or live activation',
        versions=dict(corpus=sha(corpus),fields=sha(fields),queries={p.name:sha(p) for p in sets},e5_download_manifest=sha(e5path/'download-manifest.json'),
            minilm_files={p.relative_to(minipath).as_posix():sha(p) for p in minipath.rglob('*') if p.is_file()},
            code={p:sha(ROOT/p) for p in ['scripts/evaluate_text_encoders.py','backend/app/museum/text_encoder.py','backend/app/museum/retrieval.py','backend/app/retrieval/hybrid.py']}),
        profiles={'minilm':asdict(MINILM),'e5':asdict(E5)},
        fixed=dict(bm25_top=25,dense_top=25,rrf_k=60,rrf_chunk_top=25,final_works=5,threads=4,batch_size=32,auxiliary_fields_used=False),build={},rows=[])
    cfg=MuseumSettings(_env_file=None,museum_private_corpus=corpus,museum_embedding='local',deepseek_api_key='')
    idx=MuseumIndex(cfg,MemoryStore());before=working_set();t=time.perf_counter();await idx.start()
    report['build']['baseline_total_ms']=(time.perf_counter()-t)*1000
    report['build']['baseline_working_set_delta']=memory_delta(before)
    mini=RetrievalEncoder(idx.embedding._local_model,MINILM)
    chunks=list(idx.vectors._meta.values())
    report['versions']['chunks']=hashlib.sha256(json.dumps(chunks,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    report['chunk_count']=len(chunks)
    texts=[c['content'] for c in chunks]
    # Measure passage encoding under identical batch settings. Original vectors
    # remain the baseline, and agreement is checked rather than silently replaced.
    t=time.perf_counter();mv=await asyncio.to_thread(mini.encode_passages,texts)
    report['build']['minilm_passage_encode_ms']=(time.perf_counter()-t)*1000
    import numpy as np
    assert np.allclose(mv,np.asarray(list(idx.vectors._vecs.values())),atol=1e-5)
    del mv
    print('MiniLM baseline ready;',len(chunks),'identical chunks',flush=True)
    before=working_set();t=time.perf_counter()
    model=SentenceTransformer(str(e5path),local_files_only=True,device=str(mini.model.device))
    e5=RetrievalEncoder(model,E5)
    report['build']['e5_load_ms']=(time.perf_counter()-t)*1000
    t=time.perf_counter();vectors=await asyncio.to_thread(e5.encode_passages,texts)
    report['build']['e5_passage_encode_ms']=(time.perf_counter()-t)*1000
    e5store=MemoryVectorStore()
    for chunk,vec in zip(chunks,vectors,strict=True):await e5store.add(chunk['_id'],vec.tolist(),chunk)
    del vectors
    report['build']['e5_working_set_delta']=memory_delta(before)
    report['device']=str(mini.model.device)
    report['truncation']={name:dict(passages=truncation(enc,texts,False),queries=truncation(enc,[c['query'] for c in cases],True)) for name,enc in [('minilm',mini),('e5',e5)]}
    hybrid=HybridRetriever(idx.bm25,e5store,FusionOrder(),bm25_top=25,vector_top=25,top_k=25)
    profiles={'minilm':(mini,idx.vectors,idx.hybrid),'e5':(e5,e5store,hybrid)}
    print('E5 index ready; starting paired queries',flush=True)
    for n,case in enumerate(cases):
        for sid in case['gold']:
            assert case['evidence_anchor'] in idx.records[sid]['content']
            if case.get('source_hash'):assert case['source_hash']==idx.records[sid]['source_hash']
        row={k:case[k] for k in ['id','query','category','gold']};row['results']={}
        # Alternate order to reduce, not eliminate, warm-cache/order bias.
        for name in (['minilm','e5'] if n%2==0 else ['e5','minilm']):
            enc,store,retriever=profiles[name]
            t=time.perf_counter();vec=(await asyncio.to_thread(enc.encode_queries,[case['query']]))[0].tolist()
            hits=await retriever.retrieve(case['query'],vec)
            current=await idx._current_sources([h['source_id'] for h in hits])
            ids=[r['_id'] for r in current[:5]];ms=(time.perf_counter()-t)*1000
            if name=='minilm':assert ids==previous[case['id']],('Baseline drift',case['id'])
            dense=await store.search(vec,top_k=len(chunks))
            result=dict(ids=ids,rank=next((i for i,sid in enumerate(ids,1) if sid in case['gold']),None),ms=ms,
                dense=stage_ranks(dense,case['gold']),dense_top25=list(dict.fromkeys(h['source_id'] for h in dense[:25])))
            if case.get('known_relevant_examples'):result['example_coverage']=expectation_coverage(ids,case['known_relevant_examples'])
            row['results'][name]=result
        report['rows'].append(row)
        if (n+1)%20==0:print('Paired queries',n+1,'/',len(cases),flush=True)
    report['summary']={name:summarize(report['rows'],name) for name in profiles}
    report['by_category']={c:{name:summarize([r for r in report['rows'] if r['category']==c],name) for name in profiles}
        for c in sorted({r['category'] for r in report['rows'] if r['gold']})}
    report['changes']={}
    for cutoff in [1,5]:
        def ok(r,name):
            rank=r['results'][name]['rank'];return rank is not None and rank<=cutoff
        report['changes'][f'top{cutoff}']=dict(fixed=[r['id'] for r in report['rows'] if r['gold'] and not ok(r,'minilm') and ok(r,'e5')],
            regressed=[r['id'] for r in report['rows'] if r['gold'] and ok(r,'minilm') and not ok(r,'e5')])
    report['complete']=True
    with OUT.open('x',encoding='utf-8') as f:json.dump(report,f,ensure_ascii=False,indent=2)
    print(json.dumps(dict(summary=report['summary'],changes=report['changes'])),flush=True)


if __name__=='__main__':asyncio.run(main())
