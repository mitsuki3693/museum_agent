"""Six offline arms: 2 frozen encoders x original/filter-only/normalized dense text.

Lexical index, chunk IDs, RRF and raw evidence remain unchanged. No LLM/API calls.
"""
import asyncio
import hashlib
import json
from pathlib import Path
import time

from app.museum.config import MuseumSettings
from app.museum.retrieval import MuseumIndex,FusionOrder
from app.museum.semantic_chunks import dense_views
from app.museum.text_encoder import E5,MINILM,RetrievalEncoder
from app.retrieval.hybrid import HybridRetriever
from app.retrieval.vector_store import MemoryVectorStore
from app.storage.store import MemoryStore
from evaluate_bm25f import stage_ranks,expectation_coverage
from evaluate_text_encoders import sha,summarize

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'eval/private/semantic-chunks-v1.json'


async def main():
    import torch
    from sentence_transformers import SentenceTransformer
    if OUT.exists():raise FileExistsError('Preserve existing experiment')
    torch.set_num_threads(4)
    baseline_path=ROOT/'eval/private/text-encoders-v1.json'
    baseline=json.loads(baseline_path.read_bytes());assert baseline['complete']
    corpus=ROOT/'data/private/va-pilot-300-v1-corpus.json'
    fields=ROOT/'data/private/va-search-fields-v1.1.json'
    assert sha(corpus)==baseline['versions']['corpus'] and sha(fields)==baseline['versions']['fields']
    cases=[]
    for name,expected in baseline['versions']['queries'].items():
        path=ROOT/'eval/private'/name;assert sha(path)==expected
        doc=json.loads(path.read_bytes());assert doc['frozen'];cases+=doc['cases']
    assert len(cases)==80 and sum(bool(c['gold']) for c in cases)==72
    previous={r['id']:r for r in baseline['rows']}
    miniroot=ROOT/'models/models--sentence-transformers--paraphrase-multilingual-MiniLM-L12-v2'
    assert (miniroot/'refs/main').read_text().strip()==MINILM.revision
    for name,expected in baseline['versions']['minilm_files'].items():
        assert sha(miniroot/'snapshots'/MINILM.revision/name)==expected
    e5path=ROOT/'models/multilingual-e5-base'
    assert sha(e5path/'download-manifest.json')==baseline['versions']['e5_download_manifest']
    manifest=json.loads((e5path/'download-manifest.json').read_bytes())
    for name,meta in manifest['files'].items():assert sha(e5path/name)==meta['sha256']
    cfg=MuseumSettings(_env_file=None,museum_private_corpus=corpus,museum_embedding='local',deepseek_api_key='')
    idx=MuseumIndex(cfg,MemoryStore());await idx.start()
    original_records_hash=hashlib.sha256(json.dumps(idx.records,sort_keys=True).encode()).hexdigest()
    views=dense_views(idx.records.values())
    assert views['original']==list(idx.vectors._meta.values()),'Legacy splitting drift'
    report=dict(complete=False,human_reviewed=False,scope='Offline input-view ablation, not live answers or user accuracy',
        versions=dict(baseline=sha(baseline_path),corpus=sha(corpus),fields=sha(fields),queries=baseline['versions']['queries'],
            profiles=baseline['profiles'],code={p:sha(ROOT/p) for p in ['scripts/evaluate_semantic_chunks.py','backend/app/museum/semantic_chunks.py']}),
        fixed=dict(baseline['fixed']),views=dict(version=views['version'],counts={name:len(views[name]) for name in ['original','filtered','normalized']},
            no_body_source_ids=views['no_body_source_ids'],audit=views['audit']),build={},rows=[])
    profiles={};encoders={}
    for name,profile in [('minilm',MINILM),('e5',E5)]:
        model=idx.embedding._local_model if name=='minilm' else SentenceTransformer(str(e5path),local_files_only=True,device='cpu')
        encoder=RetrievalEncoder(model,profile);encoders[name]=encoder
        t=time.perf_counter()
        if name=='minilm':original_vectors=idx.vectors._vecs
        else:
            array=await asyncio.to_thread(encoder.encode_passages,[c['content'] for c in views['original']])
            original_vectors={c['_id']:v.tolist() for c,v in zip(views['original'],array,strict=True)}
            del array
        report['build'][name]=dict(original_encode_ms=(time.perf_counter()-t)*1000,reused_original_vectors=name=='minilm')
        old_text={c['_id']:c['content'] for c in views['original']}
        changed=[c for c in views['normalized'] if c['content']!=old_text[c['_id']]]
        t=time.perf_counter();array=await asyncio.to_thread(encoder.encode_passages,[c['content'] for c in changed])
        replacements={c['_id']:v.tolist() for c,v in zip(changed,array,strict=True)};del array
        report['build'][name].update(normalized_incremental_encode_ms=(time.perf_counter()-t)*1000,changed_chunks=len(changed))
        for view in ['original','filtered','normalized']:
            store=MemoryVectorStore()
            for chunk in views[view]:
                vectors=replacements if view=='normalized' and chunk['_id'] in replacements else original_vectors
                await store.add(chunk['_id'],vectors[chunk['_id']],chunk)
            retriever=HybridRetriever(idx.bm25,store,FusionOrder(),bm25_top=25,vector_top=25,top_k=25)
            profiles[name+'_'+view]=(name,store,retriever)
        print(name,'three dense views ready',flush=True)
    for n,case in enumerate(cases):
        row={k:case[k] for k in ['id','query','category','gold']};row['results']={}
        query_vectors={};query_ms={}
        for name in (['minilm','e5'] if n%2==0 else ['e5','minilm']):
            t=time.perf_counter();query_vectors[name]=(await asyncio.to_thread(encoders[name].encode_queries,[case['query']]))[0].tolist()
            query_ms[name]=(time.perf_counter()-t)*1000
        names=list(profiles);names=names[n%len(names):]+names[:n%len(names)]
        for arm in names:
            name,store,retriever=profiles[arm];vec=query_vectors[name]
            t=time.perf_counter();hits=await retriever.retrieve(case['query'],vec)
            current=await idx._current_sources([h['source_id'] for h in hits]);ids=[r['_id'] for r in current[:5]]
            ms=query_ms[name]+(time.perf_counter()-t)*1000
            if arm.endswith('_original'):assert ids==previous[case['id']]['results'][name]['ids'],('Baseline drift',case['id'],name)
            dense=await store.search(vec,top_k=len(views['original']))
            result=dict(ids=ids,rank=next((i for i,sid in enumerate(ids,1) if sid in case['gold']),None),ms=ms,
                dense=stage_ranks(dense,case['gold']),dense_top25=list(dict.fromkeys(h['source_id'] for h in dense[:25])))
            if case.get('known_relevant_examples'):result['example_coverage']=expectation_coverage(ids,case['known_relevant_examples'])
            row['results'][arm]=result
        report['rows'].append(row)
        if (n+1)%20==0:print('Compared',n+1,'/',len(cases),flush=True)
    assert original_records_hash==hashlib.sha256(json.dumps(idx.records,sort_keys=True).encode()).hexdigest()
    report['summary']={arm:summarize(report['rows'],arm) for arm in profiles}
    report['by_category']={c:{arm:summarize([r for r in report['rows'] if r['category']==c],arm) for arm in profiles}
        for c in sorted({r['category'] for r in report['rows'] if r['gold']})}
    report['changes']={}
    for name in encoders:
        for old,new in [('original','filtered'),('filtered','normalized'),('original','normalized')]:
            for cutoff in [1,5]:
                def ok(row,view):
                    rank=row['results'][name+'_'+view]['rank'];return rank is not None and rank<=cutoff
                report['changes'][f'{name}:{old}->{new}:top{cutoff}']=dict(
                    fixed=[r['id'] for r in report['rows'] if r['gold'] and not ok(r,old) and ok(r,new)],
                    regressed=[r['id'] for r in report['rows'] if r['gold'] and ok(r,old) and not ok(r,new)])
    report['complete']=True
    with OUT.open('x',encoding='utf-8') as f:json.dump(report,f,ensure_ascii=False,indent=2)
    print(json.dumps(dict(summary=report['summary'],changes=report['changes'])),flush=True)


if __name__=='__main__':asyncio.run(main())
