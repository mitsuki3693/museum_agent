"""Narrow field exclusion against both frozen corpus/query snapshots.

All arms rebuild with the same local model and encode path; no live configuration
changes, paid APIs, new translations or answer generation.
"""
import asyncio
import argparse
import json
from pathlib import Path
import time

from app.museum.config import MuseumSettings
from app.museum.retrieval import MuseumIndex, FusionOrder
from app.museum.semantic_chunks import dense_views, administrative_view, ADMIN_LABELS
from app.museum.text_encoder import MINILM
from app.retrieval.hybrid import HybridRetriever
from app.retrieval.vector_store import MemoryVectorStore
from app.storage.store import MemoryStore
from evaluate_metadata_queries import sha, save_new, summary
from evaluate_bm25f import stage_ranks

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'eval/private/administrative-filter-v1.json'


async def main():
    import torch
    torch.set_num_threads(4)
    if OUT.exists():raise FileExistsError('Preserve experiment')
    query_path=ROOT/'eval/private/metadata-queries-v1.json'
    prior_path=ROOT/'eval/private/metadata-query-results-v1.json'
    prior=json.loads(prior_path.read_bytes());queries=json.loads(query_path.read_bytes())
    assert prior['complete'] and queries['frozen'] and prior['query_sha256']==sha(query_path)
    model_root=ROOT/'models/models--sentence-transformers--paraphrase-multilingual-MiniLM-L12-v2'
    assert (model_root/'refs/main').read_text().strip()==MINILM.revision
    model_baseline=json.loads((ROOT/'eval/private/text-encoders-v1.json').read_bytes())
    for name,expected in model_baseline['versions']['minilm_files'].items():
        assert sha(model_root/'snapshots'/MINILM.revision/name)==expected
    report=dict(complete=False,query_sha256=sha(query_path),prior_sha256=sha(prior_path),
        model_revision=MINILM.revision,excluded_labels=sorted(ADMIN_LABELS),
        encoding='Independent document batch rebuilds; shared query vector across arms',
        code_sha256={p:sha(ROOT/p) for p in ['scripts/evaluate_administrative_filter.py','backend/app/museum/semantic_chunks.py']},corpora={})
    for name,corpus in queries['corpora'].items():
        private,public=Path(corpus['private_path']),Path(corpus['public_path'])
        assert sha(private)==corpus['private_sha256'] and sha(public)==corpus['public_sha256']
        index=MuseumIndex(MuseumSettings(_env_file=None,museum_corpus=public,museum_private_corpus=private,
            museum_embedding='local',museum_dense_view='original',deepseek_api_key=''),MemoryStore())
        await index.start()
        views=dense_views(index.records.values());selective=administrative_view(index.records.values())
        assert views['original']==list(index.vectors._meta.values())
        stores={'original':index.vectors}
        for arm,chunks in [('filtered',views['filtered']),('administrative',selective['chunks'])]:
            store=MemoryVectorStore()
            vectors=await asyncio.to_thread(index.embedding._local_embed,[c['content'] for c in chunks])
            for c,vector in zip(chunks,vectors,strict=True):await store.add(c['_id'],vector,c)
            stores[arm]=store
        retrievers={arm:HybridRetriever(index.bm25,store,FusionOrder(),bm25_top=25,vector_top=25,top_k=25)
                    for arm,store in stores.items()}
        rows=[]
        for n,case in enumerate(prior['corpora'][name]['rows']):
            row={k:case[k] for k in ['id','query','gold','kind','category','language']}
            row['results']={}
            start=time.perf_counter();vector=index.embedding._local_embed([case['query']])[0]
            encode_ms=(time.perf_counter()-start)*1000
            names=list(stores);names=names[n%3:]+names[:n%3]
            for arm in names:
                start=time.perf_counter();hits=await retrievers[arm].retrieve(case['query'],vector)
                ids=[s['_id'] for s in (await index._current_sources([h['source_id'] for h in hits]))[:5]]
                ms=encode_ms+(time.perf_counter()-start)*1000
                if arm in case['results']:
                    assert ids==case['results'][arm]['ids'], (name,case['id'],arm,'baseline drift')
                dense=await stores[arm].search(vector,top_k=len(stores[arm]._meta))
                row['results'][arm]=dict(ids=ids,rank=next((i for i,s in enumerate(ids,1) if s in case['gold']),None),
                                        ms=ms,dense=stage_ranks(dense,case['gold']))
            rows.append(row)
        sections={section:{arm:summary([r for r in rows if r['kind']==section or r['category']==section],arm)
                           for arm in stores} for section in ['facet','named','old','author','material','date']}
        changes={}
        for baseline in ['original','filtered']:
            for cutoff in [1,5]:
                def ok(row,arm):
                    rank=row['results'][arm]['rank'];return rank is not None and rank<=cutoff
                changes[f'{baseline}_to_administrative_top{cutoff}']=dict(
                    fixed=[r['id'] for r in rows if r['gold'] and not ok(r,baseline) and ok(r,'administrative')],
                    regressed=[r['id'] for r in rows if r['gold'] and ok(r,baseline) and not ok(r,'administrative')])
        report['corpora'][name]=dict(source_hashes=corpus,counts={a:len(s._meta) for a,s in stores.items()},
            audit=selective['excluded'],rows=rows,summary=sections,changes=changes,
            no_new_top5_regressions=not changes['original_to_administrative_top5']['regressed'])
        print(json.dumps(dict(corpus=name,counts=report['corpora'][name]['counts'],summary=sections,changes=changes)),flush=True)
    report['complete']=True
    save_new(OUT,report)


async def verify_integration():
    """Check the actual opt-in application entry against the completed experiment."""
    import torch
    torch.set_num_threads(4)
    out=ROOT/'eval/private/administrative-filter-integration-v1.json'
    if out.exists():raise FileExistsError('Preserve integration evidence')
    experiment=json.loads(OUT.read_bytes());assert experiment['complete']
    result=dict(experiment_sha256=sha(OUT),corpora={},code_sha256={p:sha(ROOT/p) for p in
        ['backend/app/museum/retrieval.py','backend/app/museum/semantic_chunks.py','backend/app/museum/config.py']})
    for name,corpus in experiment['corpora'].items():
        source=corpus['source_hashes'];private,public=Path(source['private_path']),Path(source['public_path'])
        assert sha(private)==source['private_sha256'] and sha(public)==source['public_sha256']
        idx=MuseumIndex(MuseumSettings(_env_file=None,museum_corpus=public,museum_private_corpus=private,
            museum_embedding='local',museum_dense_view='administrative',deepseek_api_key=''),MemoryStore())
        await idx.start()
        differences=[]
        for row in corpus['rows']:
            ids=[r['_id'] for r in await idx.search(row['query'])]
            if ids!=row['results']['administrative']['ids']:differences.append(row['id'])
        result['corpora'][name]=dict(cases=len(corpus['rows']),differences=differences,chunks=len(idx.vectors._meta))
        print(json.dumps({name:result['corpora'][name]}),flush=True)
    save_new(out,result)
    assert all(not c['differences'] for c in result['corpora'].values())


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--verify-integration',action='store_true')
    args=parser.parse_args()
    asyncio.run(verify_integration() if args.verify_integration else main())
