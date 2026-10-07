"""Private acceptance: cached-score parity first, real local HTTP path second.

No paid LLM, no production database or active web configuration is used.
"""
import asyncio
import json
from pathlib import Path
import time
from uuid import uuid4

from app.museum.config import MuseumSettings
from app.museum.retrieval import MuseumIndex
from app.storage.store import MemoryStore
from app.museum.api import create_app
from fastapi.testclient import TestClient
from evaluate_metadata_queries import sha,save_new

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'eval/private/reranker-service-acceptance-v1.json'


def config(spec):
    return MuseumSettings(_env_file=None,museum_storage='memory',deepseek_api_key='',
        museum_corpus=Path(spec['public_path']),museum_private_corpus=Path(spec['private_path']),
        museum_embedding='local',museum_dense_view='original',museum_text_rerank=True,
        museum_rerank_timeout=20)


async def parity(baseline, results):
    rows=[]
    for name,corpus in baseline['corpora'].items():
        spec=corpus['source_hashes']
        assert sha(Path(spec['public_path']))==spec['public_sha256'] and sha(Path(spec['private_path']))==spec['private_sha256']
        index=MuseumIndex(config(spec),MemoryStore());await index.start()
        for old in corpus['rows']:
            expected=results[(name,old['id'])]
            class CachedScores:
                state='ready';busy=False;called=False;max_score_delta=0
                async def rank(self,q,candidates,sources):
                    self.called=True
                    assert [c['source_id'] for c in candidates]==[c['source_id'] for c in old['work_evidence']]
                    for a,b in zip(candidates,old['work_evidence'],strict=True):
                        assert set(a['lanes'])==set(b['lanes'])
                        for lane in a['lanes']:
                            av,bv=a['lanes'][lane],b['lanes'][lane]
                            assert {k:v for k,v in av.items() if k!='best_score'}=={k:v for k,v in bv.items() if k!='best_score'}
                            delta=abs(av['best_score']-bv['best_score'])
                            self.max_score_delta=max(self.max_score_delta,delta)
                            assert delta<1e-6
                    return expected['ids'],dict(status='cached_score_parity')
            cached=CachedScores();index.reranker=cached
            output,trace=await index.search_for_answer(old['query'])
            assert cached.called and trace['status']=='cached_score_parity',(name,old['id'],trace,cached.max_score_delta)
            assert [s['_id'] for s in output]==expected['ids'][:5]
            rows.append(dict(corpus=name,id=old['id'],passed=True,max_score_delta=cached.max_score_delta))
        print(json.dumps(dict(stage='cached_score_parity',corpus=name,passed=len(corpus['rows']))),flush=True)
    return rows


def main():
    import torch
    torch.set_num_threads(4)
    if OUT.exists():raise FileExistsError('Preserve acceptance report')
    baseline_path=ROOT/'eval/private/work-fusion-v1.json'
    results_path=ROOT/'eval/private/reranker-results-v3.json'
    baseline=json.loads(baseline_path.read_bytes());prior=json.loads(results_path.read_bytes());assert prior['complete']
    results={(r['corpus'],r['id']):r for r in prior['rows']}
    report=dict(complete=False,baseline_sha256=sha(baseline_path),results_sha256=sha(results_path),
                parity=asyncio.run(parity(baseline,results)),http=[],real_generation=False,production_config_changed=False)
    cfg=config(baseline['corpora']['scale300']['source_hashes'])
    app=create_app(cfg)
    with TestClient(app) as client:
        health=client.get('/api/museum/health').json()
        assert health['text_rerank']['state']=='ready',health['text_rerank']
        service=app.state.index.reranker;proc=service.process
        token=client.post('/api/museum/sessions').json()['token']
        headers={'Authorization':'Bearer '+token}
        def chat(query):
            started=time.perf_counter()
            response=client.post('/api/museum/chat',headers=headers,json=dict(query=query,request_id=str(uuid4())))
            assert response.status_code==200,response.status_code
            result=response.json()
            trace=client.portal.call(app.state.store.get,'museum_traces',result['trace_id'])
            return result,trace,(time.perf_counter()-started)*1000
        for ident in ['old-F07','old-G15','old-T15']:
            case=results[('scale300',ident)]
            result,trace,ms=chat(case['query'])
            assert trace['rerank']['status']=='applied',trace['rerank']['status']
            assert result['retrieved_ids']==case['ids'][:5]
            report['http'].append(dict(id=ident,status='applied',retrieved_ids=result['retrieved_ids'],
                                      rerank=trace['rerank'],http_ms=ms,budget_ms=20000))
            print(json.dumps(dict(stage='real_http',id=ident,status='applied',ms=round(ms))),flush=True)
        service.timeout=8
        result,trace,ms=chat(results[('scale300','old-G15')]['query'])
        report['http'].append(dict(id='default_budget',status=trace['rerank']['status'],rerank=trace['rerank'],http_ms=ms))
        # Force a real timeout and prove the process was killed. Restart only
        # within this isolated acceptance if the 8s probe already tripped it.
        if service.state!='ready':client.portal.call(service.start)
        service.timeout=.001;proc=service.process
        query=results[('scale300','old-F07')]['query']
        original=client.portal.call(app.state.index.search,query)
        result,trace,ms=chat(query)
        assert trace['rerank']['status']=='timeout'
        assert result['retrieved_ids']==[s['_id'] for s in original]
        assert proc.returncode is not None
        report['http'].append(dict(id='forced_timeout',status='timeout',http_ms=ms,worker_exited=True,
                                  fallback_unchanged=True,rerank=trace['rerank']))
        result,trace,ms=chat(query)
        assert trace['rerank']['status']=='disabled_after_timeout' and result['retrieved_ids']==[s['_id'] for s in original]
        report['http'].append(dict(id='after_timeout',status=trace['rerank']['status'],http_ms=ms))
        cfg.museum_text_rerank=False
        result,trace,ms=chat(query)
        assert trace['rerank']['status']=='disabled' and result['retrieved_ids']==[s['_id'] for s in original]
        report['http'].append(dict(id='switch_off',status='disabled',http_ms=ms))
    report['complete']=True;save_new(OUT,report)
    print(json.dumps(dict(complete=True,parity=len(report['parity']),http_checks=len(report['http']),sha256=sha(OUT))),flush=True)


if __name__=='__main__':main()
