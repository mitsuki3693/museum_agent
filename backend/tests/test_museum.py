import asyncio
import json
import time
import uuid
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from app.museum.api import create_app
from app.museum.config import MuseumSettings
from app.museum.engine import MuseumEngine, Draft, evidence_issues
from app.museum.retrieval import MuseumIndex
from app.storage.store import MemoryStore

@pytest.fixture
def museum_settings(tmp_path):
    corpus = tmp_path / "corpus.json"
    corpus.write_text(json.dumps([{"_id":"test-1", "title":"Test Vase", "content":"Material: bronze. Date: 1884.",
        "source_url":"https://example.org/collection/1", "source_hash":"fixture", "status":"active", "license":"CC0-1.0", "fetched_at":"2026-09-28"}]),encoding="utf-8")
    return MuseumSettings(_env_file=None, museum_corpus=corpus, museum_embedding="lexical", museum_storage="memory", deepseek_api_key="")

class FakeClient:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.calls = 0
    async def complete_json(self, messages):
        self.calls += 1
        value = next(self.replies)
        if isinstance(value, Exception):
            raise value
        return value

def draft(quote="Material: bronze.", text="材质为青铜。", source_id="test-1"):
    return {"abstain":False,"claims":[{"text":text,"source_id":source_id,"quote":quote}]}

async def engine(config, replies):
    config.deepseek_api_key = "test-only"
    store = MemoryStore()
    index = MuseumIndex(config, store)
    await index.start()
    fake = FakeClient(replies)
    return MuseumEngine(config,store,index,lambda:fake), store, fake

@pytest.mark.asyncio
async def test_no_unverified_final_retry_is_shown(museum_settings):
    e, store, fake = await engine(museum_settings,[draft(),{"passed":False,"issues":["unsupported"]},draft(),{"passed":False,"issues":["unsupported"]}])
    result = await e.answer("材质是什么",{"_id":"s"},"brief","test-1")
    assert fake.calls == 4
    assert result["status"] == "verification_failed"
    assert result["claims"] == [] and not result["verification"]["passed"]
    trace = await store.get("museum_traces",result["trace_id"])
    assert len(trace["attempts"]) == 2

@pytest.mark.asyncio
async def test_forged_quote_rejected_without_semantic_call(museum_settings):
    e, _, fake = await engine(museum_settings,[draft("Material: gold."),draft("Material: gold.")])
    result = await e.answer("material",{"_id":"s"},"brief","test-1")
    assert fake.calls == 2
    assert result["status"] == "verification_failed"

@pytest.mark.asyncio
async def test_verifier_outage_is_not_a_pass(museum_settings):
    e, _, _ = await engine(museum_settings,[draft(),TimeoutError()])
    result = await e.answer("material",{"_id":"s"},"brief","test-1")
    assert result["status"] == "service_unavailable"
    assert not result["verification"]["passed"]

@pytest.mark.asyncio
async def test_only_supported_sources_are_returned_and_followups_not_negative(museum_settings):
    e, store, _ = await engine(museum_settings,[draft(),{"passed":True,"issues":[]}])
    current={"_id":"s"}
    result = await e.answer("material",current,"brief","test-1")
    assert result["status"] == "answered" and len(result["sources"]) == 1
    assert current["object_id"] == "test-1"
    assert await store.count("museum_feedback") == 0

@pytest.mark.asyncio
async def test_archived_source_cannot_be_returned(museum_settings):
    store=MemoryStore()
    index=MuseumIndex(museum_settings,store)
    await index.start()
    source=await store.get("museum_sources","test-1")
    source["status"]="archived"
    await store.upsert("museum_sources",source)
    assert await index.search("bronze","test-1") == []

def test_strict_verdict_rejects_truthy_strings():
    from app.museum.engine import Verdict
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        Verdict.model_validate({"passed":"false","issues":[]})

def get_session(client):
    token=client.post('/api/museum/sessions').json()['token']
    return {"Authorization":"Bearer "+token}

def test_guest_isolation_idempotency_feedback_and_clear(museum_settings):
    with TestClient(create_app(museum_settings)) as client:
        a,b=get_session(client),get_session(client)
        body={"query":"material","object_id":"test-1","request_id":str(uuid.uuid4())}
        assert client.post('/api/museum/chat',json=body).status_code == 401
        first=client.post('/api/museum/chat',headers=a,json=body)
        assert first.status_code == 200
        assert first.json()['status']=='retrieval_only'
        again=client.post('/api/museum/chat',headers=a,json=body)
        assert first.json()['trace_id']==again.json()['trace_id']
        conflict=client.post('/api/museum/chat',headers=a,json={**body,'query':'date'})
        assert conflict.status_code == 409
        fb={"trace_id":first.json()['trace_id'],"kind":"wrong_fact"}
        assert client.post('/api/museum/feedback',headers=b,json=fb).status_code==404
        assert client.post('/api/museum/feedback',headers=a,json=fb).status_code==200
        assert client.get('/api/museum/admin/traces',headers=a).status_code==403
        assert client.delete('/api/museum/session',headers=a).status_code==200
        assert client.post('/api/museum/chat',headers=a,json=body).status_code==401
        assert client.post('/api/museum/feedback',headers=b,json=fb).status_code==404

def test_expired_session_and_unknown_object(museum_settings):
    museum_settings.museum_session_ttl=-1
    with TestClient(create_app(museum_settings)) as client:
        a=get_session(client)
        assert client.post('/api/museum/chat',headers=a,json={"query":"hello","request_id":str(uuid.uuid4())}).status_code==401

def test_blank_query_and_unknown_object(museum_settings):
    with TestClient(create_app(museum_settings)) as client:
        a=get_session(client)
        for data in [{"query":" "},{"query":"材质","object_id":"unknown"}]:
            assert client.post('/api/museum/chat',headers=a,json={**data,"request_id":str(uuid.uuid4())}).status_code==422

@pytest.mark.asyncio
async def test_confirmed_object_excludes_other_retrieval_hits(museum_settings):
    e, _, _ = await engine(museum_settings,[draft(),{"passed":True,"issues":[]}])
    original=e.index.search
    async def mixed(*args):
        hits=await original(*args)
        return hits + [{**hits[0],"_id":"other-object","title":"Other work"}]
    e.index.search=mixed
    result=await e.answer("介绍这件作品",{"_id":"s"},"brief","test-1")
    assert result['retrieved_ids']==['test-1']

@pytest.mark.asyncio
@pytest.mark.parametrize("mode,limit", [("brief",2),("deep",5)])
async def test_chosen_depth_caps_model_overproduction(museum_settings,mode,limit):
    excessive={"abstain":False,"claims":[draft()["claims"][0] for _ in range(6)]}
    e,store,_=await engine(museum_settings,[excessive,{"passed":True,"issues":[]}])
    result=await e.answer("介绍一下",{"_id":"s"},mode,"test-1")
    assert result["status"]=="answered"
    assert len(result["claims"])==limit
    trace=await store.get("museum_traces",result["trace_id"])
    assert trace["attempts"][0]["omitted_claims"]==6-limit

@pytest.mark.asyncio
async def test_visual_description_offers_candidates_without_inventing_a_story(museum_settings):
    e, store, fake = await engine(museum_settings, [{"intent":"find_artwork","candidate_ids":["test-1"]}])
    session={"_id":"description"}
    result=await e.answer("一个bronze花瓶",session,"brief",None)
    assert result["status"] == "needs_confirmation"
    assert [c["id"] for c in result["candidates"]] == ["test-1"]
    assert result["claims"] == [] and fake.calls == 1
    assert "object_id" not in session  # Finding a candidate is not user confirmation.

@pytest.mark.asyncio
async def test_description_cannot_offer_an_id_outside_retrieved_sources(museum_settings):
    e, _, _ = await engine(museum_settings, [{"intent":"find_artwork","candidate_ids":["invented"]}])
    result=await e.answer("一个bronze花瓶",{"_id":"description"},"brief",None)
    assert result["status"] == "service_unavailable"
    assert not result.get("candidates")

@pytest.mark.asyncio
async def test_factual_question_still_uses_evidence_review(museum_settings):
    e, _, fake = await engine(museum_settings, [{"intent":"question","candidate_ids":[]},draft(),{"passed":True,"issues":[]}])
    result=await e.answer("Test Vase是什么材质？",{"_id":"question"},"brief",None)
    assert result["status"] == "answered" and fake.calls == 3

@pytest.mark.asyncio
async def test_rejected_candidate_can_be_refined_in_same_conversation(museum_settings):
    e, _, fake = await engine(museum_settings,[{"query":"bronze vase"},{"intent":"find_artwork","candidate_ids":["test-1"]}])
    session={"_id":"refine","history":[{"role":"user","content":"a vase"},{"role":"assistant","content":"请确认作品"}]}
    result=await e.answer("不是，青铜的",session,"brief",None)
    assert result["status"] == "needs_confirmation" and fake.calls == 2
    assert "object_id" not in session
