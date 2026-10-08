import asyncio
import json
import sys
import time
from types import SimpleNamespace

import pytest

from app.museum.rerank_service import RerankService
from app.museum.retrieval import MuseumIndex
from app.museum.config import MuseumSettings
from app.museum.engine import MuseumEngine
from app.museum.rerank_worker import score_request
from app.museum.semantic_chunks import dense_views
from app.retrieval.vector_store import MemoryVectorStore
from app.storage.store import MemoryStore
from app.museum.config import ROOT


def test_relative_model_path_is_anchored_before_worker_changes_directory():
    svc=RerankService('models/example')
    assert svc.command[-1]==str(ROOT/'models/example')


def test_length_batching_is_explicit_and_default_off():
    assert MuseumSettings(_env_file=None).museum_rerank_sort_by_length is False
    assert RerankService('models/example').batching=='original'
    service=RerankService('models/example',sort_by_length=True)
    assert service.command[-1]=='--sort-by-length' and service.batching=='length'


@pytest.mark.asyncio
async def test_worker_batching_mismatch_is_not_reported_as_applied():
    service=RerankService('',sort_by_length=True,command=worker(
        " print(json.dumps({'request_id':r['request_id'],'ids':['a'],'batching':'original'}),flush=True)"))
    await service.start();process=service.process
    ids,trace=await service.rank('q',[dict(source_id='a')],[])
    assert ids is None and trace['status']=='error' and process.returncode is not None


def test_worker_threads_explicit_batching_into_real_score_contract():
    source=dict(_id='a',title='Vase',content='briefDescription: blue vase',source_hash='h',status='active')
    chunk=dense_views([source])['original'][0]
    candidate=dict(source_id='a',lanes={'dense':dict(best_chunk_id=chunk['_id'],best_content=chunk['content'])})
    class Model:
        def tokenizer(self,q,p,**kwargs):return {'input_ids':p.split()}
        def score(self,q,passages,*,sort_by_length=False):
            assert sort_by_length
            return dict(scores=[1.],truncated=0,token_lengths=[10])
    reply=score_request(Model(),dict(query='blue',sources=[source],candidates=[candidate]),sort_by_length=True)
    assert reply['ids']==['a'] and reply['batching']=='length'
    assert reply['ranking_policy']=='identical-evidence-stable-v1'


@pytest.mark.asyncio
async def test_length_worker_without_tie_policy_falls_back():
    svc=RerankService('',sort_by_length=True,command=worker(
        " print(json.dumps({'request_id':r['request_id'],'ids':['a'],'batching':'length'}),flush=True)"))
    await svc.start();proc=svc.process
    ids,trace=await svc.rank('q',[dict(source_id='a')],[])
    assert ids is None and trace['status']=='error' and proc.returncode is not None


@pytest.mark.asyncio
async def test_missing_evidence_controls_policy_falls_back():
    svc=RerankService('',evidence_controls=True,command=worker(
        " print(json.dumps({'request_id':r['request_id'],'ids':['a'],'batching':'original','ranking_policy':'identical-evidence-stable-v1'}),flush=True)"))
    await svc.start();proc=svc.process
    ids,trace=await svc.rank('q',[dict(source_id='a')],[])
    assert ids is None and trace['status']=='error' and proc.returncode is not None


def test_worker_stabilizes_same_evidence_only_in_opt_in_path():
    sources=[dict(_id=i,title='Vase',content='briefDescription: blue vase',source_hash=i,status='active') for i in ['a','b']]
    candidates=[]
    for source in sources:
        chunk=dense_views([source])['original'][0]
        candidates.append(dict(source_id=source['_id'],lanes={'dense':dict(best_chunk_id=chunk['_id'],best_content=chunk['content'])}))
    class Model:
        def tokenizer(self,q,p,**kwargs):return {'input_ids':p.split()}
        def score(self,q,passages,**kwargs):
            assert passages[0]==passages[1]
            return dict(scores=[1.,1.000001],truncated=0,token_lengths=[10,10])
    request=dict(query='vase',sources=sources,candidates=candidates)
    assert score_request(Model(),request)['ids']==['b','a']
    fixed=score_request(Model(),request,sort_by_length=True)
    assert fixed['ids']==['a','b'] and fixed['evidence_ties'][0]['source_ids']==['a','b']


@pytest.mark.asyncio
async def test_experimental_lane_preserves_its_own_encoding_batch(tmp_path,monkeypatch):
    # Emulate batch-dependent numerical output: sharing vectors from a different
    # document sequence must not silently alter a frozen retrieval experiment.
    class BatchSensitiveEmbedding:
        def __init__(self,settings):pass
        def _local_embed(self,texts):
            vector=[0.,1.] if any('Museum number:' in t for t in texts) else [1.,0.]
            return [vector for _ in texts]
    monkeypatch.setattr('app.museum.retrieval.EmbeddingClient',BatchSensitiveEmbedding)
    monkeypatch.setitem(sys.modules,'sentence_transformers',SimpleNamespace(SentenceTransformer=lambda *a,**k:object()))
    path=tmp_path/'batch.json'
    path.write_text(json.dumps([dict(_id='a',title='Vase',content='Museum number: C.1-2000\nDate: 1700\nBlue vase',status='active',source_hash='a')]))
    cfg=MuseumSettings(_env_file=None,museum_corpus=path,museum_embedding='local',museum_text_rerank=True)
    index=MuseumIndex(cfg,MemoryStore());await index.start()
    assert all(v==[0.,1.] for v in index.vectors._vecs.values())
    assert all(v==[1.,0.] for v in index.rerank_vectors._vecs.values())
    assert set(index.rerank_vectors._meta)=={'a::1','a::2'}


def worker(body):
    script="import sys,json,time\nprint(json.dumps({'ready':True}),flush=True)\nfor line in sys.stdin:\n r=json.loads(line)\n"+body
    return [sys.executable,'-u','-c',script]


@pytest.mark.asyncio
async def test_real_worker_protocol_success_and_cleanup():
    svc=RerankService('',command=worker(" print(json.dumps({'request_id':r['request_id'],'ids':['b','a']}),flush=True)"))
    await svc.start();proc=svc.process
    try:
        ids,trace=await svc.rank('vase',[dict(source_id='a'),dict(source_id='b')],[])
        assert ids==['b','a'] and trace['status']=='applied'
    finally:await svc.stop()
    assert proc.returncode is not None


@pytest.mark.asyncio
async def test_timeout_kills_process_busy_does_not_queue_and_circuit_stays_open():
    svc=RerankService('',timeout=.15,command=worker(' time.sleep(30)'))
    await svc.start();proc=svc.process
    task=asyncio.create_task(svc.rank('first',[dict(source_id='a')],[]))
    await asyncio.sleep(.02)
    before=time.perf_counter();ids,busy=await svc.rank('second',[dict(source_id='a')],[])
    assert ids is None and busy['status']=='busy' and time.perf_counter()-before<.1
    ids,trace=await task
    assert ids is None and trace['status']=='timeout' and trace['ms']<1000
    assert proc.returncode is not None and svc.process is None
    _,later=await svc.rank('later',[dict(source_id='a')],[])
    assert later['status']=='disabled_after_timeout'


@pytest.mark.asyncio
async def test_cancelled_request_kills_worker_and_propagates():
    svc=RerankService('',command=worker(' time.sleep(30)'))
    await svc.start();proc=svc.process
    task=asyncio.create_task(svc.rank('q',[dict(source_id='a')],[]))
    await asyncio.sleep(.02);task.cancel()
    with pytest.raises(asyncio.CancelledError):await task
    assert proc.returncode is not None and not svc.busy


@pytest.mark.asyncio
@pytest.mark.parametrize('payload',[{'ids':['forged']},{'ids':['a','a']},{'error':'bad'},{}])
async def test_invalid_worker_identity_or_output_falls_back(payload):
    svc=RerankService('',command=worker(f" print(json.dumps(dict(request_id=r['request_id'],**{payload!r})),flush=True)"))
    await svc.start();proc=svc.process
    ids,trace=await svc.rank('q',[dict(source_id='a')],[])
    assert ids is None and trace['status']=='error' and proc.returncode is not None


@pytest.mark.asyncio
async def test_failed_startup_does_not_crash_service():
    svc=RerankService('',command=[sys.executable,'-c','print("bad-json")'])
    await svc.start()
    assert svc.state=='unavailable' and svc.process is None


@pytest.mark.asyncio
async def test_hung_startup_is_bounded_and_killed():
    svc=RerankService('',startup_timeout=.15,command=[sys.executable,'-c','import time; time.sleep(30)'])
    task=asyncio.create_task(svc.start())
    for _ in range(50):
        if svc.process:break
        await asyncio.sleep(.01)
    proc=svc.process;await task
    assert proc is not None and proc.returncode is not None and svc.state=='unavailable'


async def fixture_index(tmp_path):
    records=[dict(_id=i,title='Vase '+i,content='Maker: Alice\nbriefDescription: blue vase '+i,
        source_hash=i,status='active',fields={'accession_number':f'C.{n}-2000'},
        source_url='https://example.org/'+i,license='fixture',fetched_at='2026-10-07') for n,i in enumerate(['a','b'],1)]
    path=tmp_path/'corpus.json';path.write_text(json.dumps(records),encoding='utf-8')
    config=MuseumSettings(_env_file=None,museum_corpus=path,museum_embedding='lexical',museum_text_rerank=True,deepseek_api_key='')
    store=MemoryStore();index=MuseumIndex(config,store);await index.start()
    index.embedding=SimpleNamespace(_local_embed=lambda texts:[[1.,0.] for _ in texts])
    index.rerank_vectors=MemoryVectorStore()
    for c in dense_views(records)['original']:
        await index.vectors.add(c['_id'],[1.,0.],c)
        await index.rerank_vectors.add(c['_id'],[1.,0.],c)
    return index,store,config


@pytest.mark.asyncio
async def test_answer_opt_in_bypass_original_photo_search_and_trace(tmp_path):
    index,store,config=await fixture_index(tmp_path)
    class Service:
        state='ready';busy=False;calls=0
        async def rank(self,q,c,s):
            self.calls+=1
            return [x['source_id'] for x in c][::-1],dict(status='applied')
    service=Service();index.reranker=service
    baseline=await index.search('blue')
    rows,trace=await index.search_for_answer('blue')
    assert [r['_id'] for r in rows]==[r['_id'] for r in baseline][::-1]
    assert trace['fallback_ids']==[r['_id'] for r in baseline]
    for q,obj,variant in [('C.1-2000',None,'hybrid'),('blue','a','hybrid'),('blue',None,'lexical')]:
        rows,trace=await index.search_for_answer(q,obj,variant)
        assert trace['status']=='bypassed'
    assert service.calls==1
    assert await index.search('blue')==baseline  # Photo caller is unchanged.
    engine=MuseumEngine(config,store,index)
    result=await engine.answer('blue',{'_id':'session'},'brief',None)
    saved=await store.get('museum_traces',result['trace_id'])
    assert saved['rerank']['status']=='applied' and 'rerank' not in result


@pytest.mark.asyncio
async def test_fallback_revalidates_archived_sources(tmp_path):
    index,store,_=await fixture_index(tmp_path)
    class Service:
        state='ready';busy=False
        async def rank(self,q,c,s):
            row=await store.get('museum_sources','a');row['status']='archived'
            await store.upsert('museum_sources',row)
            return None,dict(status='timeout')
    index.reranker=Service()
    rows,trace=await index.search_for_answer('blue')
    assert [r['_id'] for r in rows]==['b'] and trace['status']=='timeout'


def test_worker_uses_frozen_evidence_contract_without_private_fixtures():
    source=dict(_id='a',title='Vase',content='Maker: Alice\nbriefDescription: Blue vase',source_hash='fixture',status='active')
    chunks=dense_views([source])['original'];c=chunks[-1]
    candidate=dict(source_id='a',lanes={'dense':dict(best_chunk_id=c['_id'],best_content=c['content'])})
    class Model:
        def tokenizer(self,q,p,**kwargs):return {'input_ids':p.split()+q.split()}
        def score(self,q,passages):
            assert 'Blue vase' in passages[0]
            return dict(scores=[1.],truncated=0,token_lengths=[10])
    result=score_request(Model(),dict(query='blue',candidates=[candidate],sources=[source]))
    assert result['ids']==['a'] and result['evidence'][0]['source_hash']=='fixture'
    assert 'text' not in json.dumps(result['evidence'])
