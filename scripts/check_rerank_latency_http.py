"""Isolated real HTTP/model acceptance. No paid APIs or production DB writes."""
import argparse
import asyncio
import json
import math
from pathlib import Path
import socket
import time
from uuid import uuid4

import httpx
import uvicorn

from app.museum.api import create_app
from app.museum.config import ROOT
from benchmark_rerank_latency import IDS
from evaluate_bilingual_retrieval import settings,private_path,rank


async def main(args):
    import torch
    torch.set_num_threads(4)
    frozen=json.loads((ROOT/'eval/private/rerank-latency-input-v1.json').read_bytes())
    cases={r['id']:r for r in frozen['rows']}
    cfg=settings(local=True)
    cfg.museum_catalogue_glossary=True;cfg.museum_metadata_routing=True;cfg.museum_text_rerank=True
    cfg.museum_rerank_auto_recover=True;cfg.museum_rerank_sort_by_length=True
    cfg.museum_rerank_backend=args.backend;cfg.museum_rerank_context_budget=args.budget
    app=create_app(cfg)
    sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,log_level='error',access_log=False))
    task=asyncio.create_task(server.serve(sockets=[sock]))
    report=dict(complete=False,backend=args.backend,budget=args.budget,paid_api_calls=0,production_db_used=False,rows=[])
    with private_path(args.output).open('x',encoding='utf-8') as out:
        def save():
            out.seek(0);json.dump(report,out,ensure_ascii=False,indent=2);out.truncate();out.flush()
        try:
            for _ in range(3600):
                if server.started:break
                if task.done():await task;raise RuntimeError('Server startup failed')
                await asyncio.sleep(.25)
            assert server.started
            async with httpx.AsyncClient(base_url=f'http://127.0.0.1:{port}',timeout=30,trust_env=False) as client:
                svc=app.state.index.reranker
                assert svc.state=='ready'
                session=await client.post('/api/museum/sessions');session.raise_for_status()
                headers={'Authorization':'Bearer '+session.json()['token']}
                async def chat(query):
                    start=time.perf_counter()
                    response=await client.post('/api/museum/chat',json=dict(query=query,request_id=str(uuid4())),headers=headers)
                    response.raise_for_status();result=response.json()
                    assert not result['usage']
                    trace=await app.state.store.get('museum_traces',result['trace_id'])
                    return result,trace,round((time.perf_counter()-start)*1000)
                warm,trace,ms=await chat(cases[IDS[0]]['query'])
                report['warmup']=dict(ms=ms,rerank=trace['rerank']);save()
                assert trace['rerank']['status']=='applied'
                for repetition in range(3):
                    for ident in IDS:
                        result,trace,ms=await chat(cases[ident]['query'])
                        row=dict(id=ident,round=repetition+1,http_ms=ms,ids=result['retrieved_ids'],
                            rank=rank(result['retrieved_ids'],cases[ident]['gold']),rerank=trace['rerank'])
                        report['rows'].append(row);save()
                        print(json.dumps(dict(done=len(report['rows']),status=trace['rerank']['status'],http_ms=ms)),flush=True)
                        assert trace['rerank']['status']=='applied'
                # Direct routes must bypass the neural service entirely.
                direct=[]
                for query in ['C.2359-1910','找乔瓦尼·博洛尼亚的作品','找年代标注为1930的作品']:
                    result,trace,ms=await chat(query)
                    direct.append(dict(query=query,route=trace['retrieval_route'],attempts=trace['attempts'],ms=ms))
                    assert trace['retrieval_route'] in ('exact_accession','catalogue_metadata') and not trace['attempts']
                report['direct_routes']=direct
                query=cases[IDS[0]]['query'];cfg.museum_text_rerank=False
                expected=[r['_id'] for r in (await app.state.index.search_for_answer(query))[0]]
                cfg.museum_text_rerank=True;svc.timeout=.001
                result,trace,ms=await chat(query)
                assert trace['rerank']['status']=='timeout' and result['retrieved_ids']==expected
                report['forced_timeout']=dict(ms=ms,rerank=trace['rerank'],fallback_equal=True)
                svc.timeout=8
                result,trace,ms=await chat(query)
                assert result['retrieved_ids']==expected and trace['rerank']['status'] in ('recovering','starting')
                report['while_recovering']=dict(ms=ms,status=trace['rerank']['status'])
                for _ in range(480):
                    if svc.state=='ready':break
                    await asyncio.sleep(.25)
                result,trace,ms=await chat(query)
                assert trace['rerank']['status']=='applied' and svc.restarts==1 and svc.failures==0
                report['after_recovery']=dict(ms=ms,rerank=trace['rerank'])
                objects=(await client.get('/api/museum/objects')).json()
                aic=next(r for r in objects if r['id']=='artic-6565')
                assert aic['image_status']=='missing' and aic['image_reason']=='not_public_domain' and aic['image_url'] is None
                report['aic_missing_explicit']=True
            durations=sorted(r['rerank']['ms'] for r in report['rows'])
            report['p95_ms']=durations[math.ceil(len(durations)*.95)-1]
            report['latency_target_passed']=report['p95_ms']<6500
            report['complete']=True;save()
        except Exception as exc:
            report['error_type']=type(exc).__name__;save();raise
        finally:
            server.should_exit=True
            await task
            sock.close()
    print(json.dumps(dict(complete=True,p95=report['p95_ms'],acceptance=report['latency_target_passed'])),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--backend',choices=['torch','onnx','int8'],required=True)
    p.add_argument('--budget',type=int,choices=[192,256],default=256)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    if private_path(args.output).exists():raise FileExistsError('Preserve evidence')
    asyncio.run(main(args))
