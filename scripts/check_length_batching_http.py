"""Isolated loopback TCP acceptance with real embeddings, BGE and API routes.

No paid generation, production DB, .env configuration, or active website edits.
Tests the current runtime retrieval path; experimental Chinese recall remains
offline and must not be conflated with this service-lifecycle acceptance.
"""
import asyncio
import json
import socket
import time
from pathlib import Path
from uuid import uuid4

import httpx
import uvicorn
from app.museum.api import create_app
from app.museum.config import MuseumSettings
from evaluate_chinese_recall import sha

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'eval/private/length-batching-http-v1.json'


async def main():
    import torch
    torch.set_num_threads(4)
    if OUT.exists() or OUT.with_suffix('.jsonl').exists():raise FileExistsError('Preserve acceptance')
    baseline_path=ROOT/'eval/private/reranker-results-v3.json'
    baseline=json.loads(baseline_path.read_bytes());assert baseline['complete']
    assert sha(baseline_path)=='a9208a2167a8cbbe490a74d9fafdf28709054ec80badc66c2985e68865b40665'
    cases={r['id']:r for r in baseline['rows'] if r['corpus']=='scale300'}
    spec=json.loads((ROOT/'eval/private/chinese-recall-v3.json').read_bytes())['corpora']['scale300']['source_hashes']
    for p in ['public','private']:assert sha(Path(spec[p+'_path']))==spec[p+'_sha256']
    cfg=MuseumSettings(_env_file=None,museum_storage='memory',deepseek_api_key='',museum_embedding='local',
        museum_corpus=Path(spec['public_path']),museum_private_corpus=Path(spec['private_path']),
        museum_text_rerank=True,museum_rerank_sort_by_length=True,museum_rerank_timeout=20,
        museum_dense_view='original',museum_search_fields=None,museum_visual_manifest=None,museum_daily_backup=False)
    app=create_app(cfg)
    sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,log_level='error',access_log=False))
    task=asyncio.create_task(server.serve(sockets=[sock]))
    report=dict(complete=False,transport='loopback_tcp',production_config_changed=False,real_generation=False,
        production_db_used=False,api_calls=0,baseline_sha256=sha(baseline_path),corpus=spec,checks=[])
    def record(item):
        report['checks'].append(item)
        with OUT.with_suffix('.jsonl').open('a',encoding='utf-8') as f:f.write(json.dumps(item,ensure_ascii=False)+'\n')
        print(json.dumps({k:item[k] for k in ['id','status','http_ms'] if k in item}),flush=True)
    try:
        for _ in range(600):
            if server.started:break
            if task.done():await task;raise RuntimeError('Server did not start')
            await asyncio.sleep(.2)
        assert server.started,'Startup not ready'
        async with httpx.AsyncClient(base_url=f'http://127.0.0.1:{port}',timeout=120,trust_env=False) as client:
            health=(await client.get('/api/museum/health')).json()
            assert health['text_rerank']['state']=='ready' and health['text_rerank']['batching']=='length'
            svc=app.state.index.reranker
            headers=[]
            for _ in range(2):
                response=await client.post('/api/museum/sessions');response.raise_for_status()
                headers.append({'Authorization':'Bearer '+response.json()['token']})
            async def chat(query,session=0):
                started=time.perf_counter()
                response=await client.post('/api/museum/chat',headers=headers[session],
                    json=dict(query=query,request_id=str(uuid4())))
                report['last_http_status']=response.status_code
                response.raise_for_status();result=response.json()
                trace=await app.state.store.get('museum_traces',result['trace_id'])
                report['last_observation']=dict(retrieved_ids=result['retrieved_ids'],rerank=trace.get('rerank'))
                return result,trace,round((time.perf_counter()-started)*1000)
            for ident in ['old-F07','old-G15']:
                result,trace,ms=await chat(cases[ident]['query'])
                assert trace['rerank']['status']=='applied' and trace['rerank']['batching']=='length'
                assert result['retrieved_ids']==cases[ident]['ids'][:5]
                record(dict(id=ident,status='applied',http_ms=ms,budget_ms=20000,rerank=trace['rerank'],parity=True))
            query=cases['old-F07']['query']
            original=[r['_id'] for r in await app.state.index.search(query)]
            pending=asyncio.create_task(chat(query))
            for _ in range(1000):
                if svc.busy or pending.done():break
                await asyncio.sleep(.01)
            assert svc.busy,'No overlapping request reached worker'
            result,trace,ms=await chat(query,1)
            assert trace['rerank']['status']=='busy' and result['retrieved_ids']==original
            record(dict(id='busy',status='busy',http_ms=ms,fallback_unchanged=True))
            first,first_trace,first_ms=await pending
            assert first_trace['rerank']['status']=='applied'
            record(dict(id='inflight_completed',status='applied',http_ms=first_ms))
            svc.timeout=8;proc=svc.process
            result,trace,ms=await chat(cases['old-G15']['query'])
            assert trace['rerank']['status'] in ['applied','timeout']
            if trace['rerank']['status']=='timeout':assert proc.returncode is not None
            record(dict(id='eight_second_budget',status=trace['rerank']['status'],http_ms=ms,rerank=trace['rerank']))
            if svc.state!='ready':await svc.start()  # Explicit isolated test restart only.
            assert svc.state=='ready'
            svc.timeout=.001;proc=svc.process
            result,trace,ms=await chat(query)
            assert trace['rerank']['status']=='timeout' and result['retrieved_ids']==original
            assert proc.returncode is not None and svc.process is None
            record(dict(id='forced_timeout',status='timeout',http_ms=ms,worker_exited=True,
                        fallback_unchanged=True,rerank=trace['rerank']))
            result,trace,ms=await chat(query)
            assert trace['rerank']['status']=='disabled_after_timeout' and result['retrieved_ids']==original
            assert svc.process is None
            record(dict(id='after_timeout',status=trace['rerank']['status'],http_ms=ms,no_auto_restart=True))
            cfg.museum_text_rerank=False
            result,trace,ms=await chat(query)
            assert trace['rerank']['status']=='disabled' and result['retrieved_ids']==original
            record(dict(id='switch_off',status='disabled',http_ms=ms,fallback_unchanged=True))
        report['complete']=True
    except Exception as exc:
        report['error_type']=type(exc).__name__
        raise
    finally:
        server.should_exit=True
        await task
        sock.close()
        with OUT.open('x',encoding='utf-8') as f:json.dump(report,f,ensure_ascii=False,indent=2)
    print(json.dumps(dict(complete=True,checks=len(report['checks']),sha256=sha(OUT))),flush=True)


if __name__=='__main__':asyncio.run(main())
