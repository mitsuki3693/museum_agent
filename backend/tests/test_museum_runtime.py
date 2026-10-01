import asyncio
import hashlib
import json
import uuid
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from app.museum.api import create_app
from app.museum.config import MuseumSettings
from app.museum.runtime import RuntimeStore, WriteGate, export_metrics, review_rows
from app.museum.evaluation import freeze_dataset, validate_frozen, usage_cost
from app.storage.store import MemoryStore


@pytest.fixture
def config(tmp_path):
    p = tmp_path / "corpus.json"
    p.write_text(json.dumps([{"_id":"vase", "title":"Test Vase", "content":"Material: bronze.",
        "source_url":"https://example.org/1", "source_hash":"fixture", "status":"active", "license":"CC0", "fetched_at":"2026-10-01"}]))
    return MuseumSettings(_env_file=None, museum_corpus=p, museum_embedding="lexical", museum_admin_token="test-review", museum_backup_dir=tmp_path/"backups")

def session(client):
    return {"Authorization":"Bearer "+client.post("/api/museum/sessions").json()["token"]}


def test_close_keeps_traces_but_delete_removes_own_data(config):
    with TestClient(create_app(config)) as c:
        a,b=session(c),session(c)
        result=c.post('/api/museum/chat',headers=a,json={"query":"bronze","request_id":str(uuid.uuid4())}).json()
        tid=result['trace_id']
        assert c.get('/api/museum/traces/'+tid,headers=b).status_code==404
        assert c.get('/api/museum/traces/'+tid,headers=a).status_code==200
        assert c.post('/api/museum/session/close',headers=a).status_code==200
        assert c.get('/api/museum/traces/'+tid,headers=a).status_code==401
        d=c.get('/api/museum/admin/traces',headers={"Authorization":"Bearer test-review"}).json()
        assert len(d['traces'])==1


def test_admin_filter_review_and_export_do_not_leak_free_text(config):
    admin={"Authorization":"Bearer test-review"}
    with TestClient(create_app(config)) as c:
        a=session(c)
        row=c.post('/api/museum/chat',headers=a,json={"query":"private@example.com 电话13800000000","request_id":str(uuid.uuid4())}).json()
        c.post('/api/museum/feedback',headers=a,json={"trace_id":row['trace_id'],"kind":"wrong_fact","comment":"private note"})
        assert c.post('/api/museum/admin/reviews/text/'+row['trace_id'],headers=a,json={"status":"confirmed","stage":"generation"}).status_code==403
        assert c.post('/api/museum/admin/reviews/text/'+row['trace_id'],headers=admin,json={"status":"confirmed","stage":"generation","notes":"private note"}).status_code==200
        assert len(c.get('/api/museum/admin/traces?stage=generation',headers=admin).json()['traces'])==1
        assert c.get('/api/museum/admin/traces?stage=vision',headers=admin).json()['traces']==[]
        exported=c.get('/api/museum/admin/export',headers=admin).text
        for text in ['private@example.com','13800000000','private note','Material: bronze.',row['trace_id'],a['Authorization'][7:]]:
            assert text not in exported
        assert c.get('/api/museum/admin/export').status_code==403


@pytest.mark.asyncio
async def test_sources_never_reach_runtime_database_and_ttl_is_date():
    backend=MemoryStore();store=RuntimeStore(backend)
    await store.upsert('museum_sources',{'_id':'source','content':'private source'})
    assert await backend.count('museum_sources')==0
    await store.upsert('museum_sessions',{'_id':'s','expires_at':1})
    assert isinstance((await store.get('museum_sessions','s'))['purge_at'],datetime)
    claim={'_id':'s:r','request_id':'r','session_id':'s','expires_at':1}
    assert await store.claim_request(claim)
    assert not await store.claim_request(claim)


@pytest.mark.asyncio
async def test_backup_gate_waits_for_writes_and_releases_after_error():
    gate=WriteGate();active=asyncio.Event();release=asyncio.Event();started=asyncio.Event()
    async def writer():
        async with gate.operation():
            active.set();await release.wait()
    async def backup():
        async with gate.maintenance():
            started.set();raise ValueError('fixture')
    w=asyncio.create_task(writer());await active.wait();b=asyncio.create_task(backup())
    await asyncio.sleep(.01);assert not started.is_set();release.set();await w
    with pytest.raises(ValueError):await b
    async with gate.operation():assert gate.active==1


def test_frozen_dataset_rejects_mutation_and_missing_human_review():
    index=SimpleNamespace(corpus_hash='a'*64,records={})
    data={'kind':'text','cases':[]}
    for k,n in {'retrieval':20,'followup':8,'false_premise':4,'unanswerable':4,'style':4}.items():
        for i in range(n):data['cases'].append({'id':f'{k}{i}','category':k,'reviewed':False,'gold_source_ids':[]})
    with pytest.raises(ValueError,match='Human review'):freeze_dataset(data,index)
    frozen={'frozen':True,'corpus_hash':index.corpus_hash}
    frozen['dataset_hash']=hashlib.sha256(json.dumps(frozen,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    validate_frozen(frozen,index.corpus_hash)
    with pytest.raises(ValueError):validate_frozen(frozen,'b'*64)
    frozen['cases']=['tampered']
    with pytest.raises(ValueError):validate_frozen(frozen,index.corpus_hash)


def test_unknown_cost_is_not_zero():
    assert usage_cost([{'prompt_tokens':100,'completion_tokens':5,'total_tokens':105}])['api_cost'] is None


def test_photo_timeout_is_recorded(config,monkeypatch):
    config.deepseek_api_key='test-only';admin={"Authorization":"Bearer test-review"}
    async def timeout(*args):raise asyncio.TimeoutError()
    monkeypatch.setattr('app.museum.api.PhotoRecognizer.recognize',timeout)
    with TestClient(create_app(config)) as c:
        a=session(c)
        assert c.post('/api/museum/recognize',headers=a,files={'photo':('x.jpg',b'fake','image/jpeg')}).status_code==504
        rows=c.get('/api/museum/admin/traces?stage=service',headers=admin).json()['photo_traces']
        assert len(rows)==1 and rows[0]['status']=='timeout'


def test_request_crash_recovery_does_not_regenerate(config):
    app=create_app(config)
    with TestClient(app) as c:
        a=session(c);body={'query':'bronze','request_id':str(uuid.uuid4())}
        first=c.post('/api/museum/chat',headers=a,json=body).json()
        # Simulate the gap between persisted trace and completed cache.
        rows=app.state.store.backend._data['museum_request_cache']
        for row in rows.values():row.pop('result');row['state']='pending'
        assert c.post('/api/museum/chat',headers=a,json=body).json()['trace_id']==first['trace_id']
        assert c.post('/api/museum/chat',headers=a,json={**body,'query':'another'}).status_code==409


@pytest.mark.asyncio
async def test_mixed_trace_pagination_keeps_equal_timestamps():
    store=RuntimeStore(MemoryStore())
    for i in range(9):
        table='museum_traces' if i%2 else 'museum_photo_traces'
        await store.upsert(table,{'_id':str(i),'session_id':'s','created_at':100,'status':'answered'})
    seen=[];cursor={}
    while True:
        page=await review_rows(store,limit=3,**cursor)
        seen.extend(r['_id'] for r in page['traces']+page['photo_traces'])
        cursor=page['next_cursor']
        if not cursor:break
    assert len(seen)==9 and set(seen)==set(map(str,range(9)))


def test_restart_exposes_interruption_even_before_initial_trace(config,monkeypatch):
    store=RuntimeStore(MemoryStore())
    asyncio.run(store.claim_request({'_id':'s:r','session_id':'s','request_id':'r','trace_id':'lost',
        'state':'pending','created_at':1,'expires_at':9999999999,'query':'interrupted question'}))
    monkeypatch.setattr('app.museum.api.RuntimeStore',lambda *args,**kwargs:store)
    with TestClient(create_app(config)) as c:
        rows=c.get('/api/museum/admin/traces?stage=service',headers={'Authorization':'Bearer test-review'}).json()['traces']
        assert len(rows)==1 and rows[0]['status']=='interrupted' and rows[0]['_id']=='lost'


def test_evaluation_run_is_immutable_but_human_grade_can_be_added(config):
    headers={'Authorization':'Bearer test-review'}
    run={'_id':'batch','created_at':1,'kind':'text','status':'completed','dataset_version':'v1',
        'dataset_hash':'a'*64,'corpus_hash':'b'*64,'model':'fixture','prompt_version':'v1',
        'results':[{'case_id':'q1','variant':'bm25'}]}
    with TestClient(create_app(config)) as c:
        assert c.post('/api/museum/admin/eval-runs',headers=headers,json=run).status_code==200
        assert c.post('/api/museum/admin/eval-runs',headers=headers,json={**run,'model':'changed'}).status_code==409
        grade={'case_id':'q1','variant':'bm25','reviewer':'test reviewer','facts_correct':1,'facts_total':1,'citations_supported':1,'citations_total':1}
        assert c.post('/api/museum/admin/eval-runs/batch/grades',headers=headers,json=grade).json()['graded']==1
        saved=c.get('/api/museum/admin/operations',headers=headers).json()['eval_runs'][0]
        assert saved['model']=='fixture' and saved['results'][0]['human_grade']['facts_correct']==1
