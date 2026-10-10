"""Freeze the current 1000-work worker requests; profile isolated CPU ranking.

Private artifacts only, no generation APIs. Never rewrites an existing run.
"""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import time

from app.museum.config import ROOT
from app.museum.retrieval import MuseumIndex
from app.storage.store import MemoryStore
from evaluate_bilingual_retrieval import settings,versions,private_path

IDS=['M05-zh','M05-en','old-T30','old-F07','old-G01','old-G15',
     'dev-glass','dev-sappho','dev-chair','holdout-01','holdout-05','holdout-08']


async def profile(output,backend='torch',budget=256):
    from app.museum.rerank_service import RerankService
    frozen=ROOT/'eval/private/rerank-latency-input-v1.json'
    data=json.loads(frozen.read_bytes());assert data['complete']
    cases={r['id']:r for r in data['rows']}
    svc=RerankService(ROOT/'models/bge-reranker-v2-m3',timeout=30 if backend=='torch' else 8,sort_by_length=True,evidence_controls=True,
        backend=backend,onnx_path=ROOT/'models/bge-reranker-v2-m3-onnx-v1',context_budget=budget)
    # This offline diagnostic allows slow requests to finish so timing is
    # observable. It never changes the website's eight-second deadline.
    report=dict(complete=False,input_sha256=hashlib.sha256(frozen.read_bytes()).hexdigest(),
                cases=IDS,rounds=3,website_budget_unchanged=True,backend=backend,budget=budget,rows=[])
    try:
        await svc.start();assert svc.state=='ready'
        with private_path(output).open('x',encoding='utf-8') as out:
            for iteration in range(4):
                for ident in (IDS[:1] if iteration==0 else IDS):
                    row=cases[ident];req=row['request']
                    ids,trace=await svc.rank(req['query'],req['candidates'],req['sources'])
                    item=dict(id=ident,round=iteration,warmup=iteration==0,ids=ids,trace=trace)
                    report['rows'].append(item)
                    out.seek(0);json.dump(report,out,ensure_ascii=False,indent=2);out.truncate();out.flush()
                    print(json.dumps(dict(id=ident,round=iteration,status=trace['status'],ms=trace.get('ms'),
                        inference_ms=round(trace.get('profile',{}).get('inference_ms',0)))),flush=True)
                    assert ids is not None
            report['complete']=True
            out.seek(0);json.dump(report,out,ensure_ascii=False,indent=2);out.truncate()
    finally:await svc.stop()


async def freeze(output):
    import torch
    torch.set_num_threads(4)
    cfg=settings(local=True)
    cfg.museum_catalogue_glossary=True;cfg.museum_metadata_routing=True;cfg.museum_text_rerank=True
    index=MuseumIndex(cfg,MemoryStore())
    await index.start()
    class Capture:
        state='ready';busy=False;request=None
        async def rank(self,q,c,s):
            self.request=dict(query=q,candidates=c,sources=s)
            return None,dict(status='captured')
    capture=Capture();index.reranker=capture
    cases=json.loads((ROOT/'eval/private/bilingual-v1-cases.json').read_bytes())['cases']
    report=dict(complete=False,versions=versions(cfg),model=cfg.museum_embedding_model,rows=[])
    with private_path(output).open('x',encoding='utf-8') as out:
        for case in cases:
            capture.request=None
            rows,trace=await index.search_for_answer(case['query'])
            report['rows'].append(dict(**case,fallback_ids=[r['_id'] for r in rows],request=capture.request,trace=trace))
        report['complete']=True
        json.dump(report,out,ensure_ascii=False,indent=2)
    print(json.dumps(dict(frozen=len(cases),worker_requests=sum(bool(r['request']) for r in report['rows']))),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=['freeze','profile'])
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--backend',choices=['torch','onnx','int8'],default='torch')
    parser.add_argument('--budget',type=int,choices=[160,192,256],default=256)
    args=parser.parse_args()
    if private_path(args.output).exists():raise FileExistsError('Preserve evidence')
    asyncio.run(freeze(args.output) if args.mode=='freeze' else profile(args.output,args.backend,args.budget))
