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
async def test_repair_has_rejected_draft_and_still_needs_fresh_verdict(museum_settings):
    """The repair call must see what was rejected, not just detached criticisms."""
    rejected = draft(text="不是青铜，但它是青铜。")
    museum_settings.museum_answer_policy = 'repair'
    corrected = draft()
    e, store, fake = await engine(museum_settings,
        [rejected, {"passed":False,"issues":["self contradiction"]},
         corrected, {"passed":True,"issues":[]}])
    messages = []
    original = fake.complete_json
    async def capture(value):
        messages.append(value)
        return await original(value)
    fake.complete_json = capture
    result = await e.answer("它不是青铜吗？", {"_id":"repair"}, "brief", "test-1")
    repair = json.loads(messages[2][1]['content'])
    assert repair['rejected_draft'] == rejected
    assert repair['previous_issues'] == ['self contradiction']
    assert len(messages) == 4  # A repair is never accepted without verification.
    assert result['answer'] == corrected['claims'][0]['text']
    trace = await store.get('museum_traces', result['trace_id'])
    assert trace['attempts'][0]['draft'] == rejected
    assert trace['attempts'][1]['verdict']['passed'] is True

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

@pytest.mark.asyncio
@pytest.mark.parametrize('change,status', [
    ({'source_id':'other'}, 'verification_failed'),
    ({'quote':'Material: gold.'}, 'verification_failed'),
    ({'value':'gold'}, 'verification_failed'),
    ({}, 'answered'),
])
async def test_fact_selection_checks_spans_before_generation(museum_settings, change, status):
    museum_settings.museum_answer_policy = 'facts'
    fact = {'aspect':'material','scope':'production','source_id':'test-1',
            'value':'bronze','quote':'Material: bronze.', **change}
    e, store, fake = await engine(museum_settings, [{'facts':[fact]}, draft(), {'passed':True,'issues':[]}])
    result = await e.answer('材质是什么？', {'_id':'facts'}, 'brief', 'test-1')
    assert result['status'] == status
    assert fake.calls == (3 if status == 'answered' else 1)
    assert bool(result['claims']) == (status == 'answered')
    trace = await store.get('museum_traces', result['trace_id'])
    assert trace['attempts'][0]['stage'] == 'fact_selection'

@pytest.mark.asyncio
async def test_empty_facts_abstains_without_generation(museum_settings):
    museum_settings.museum_answer_policy = 'facts'
    e, _, fake = await engine(museum_settings, [{'facts':[]}])
    result = await e.answer('早餐吃什么？', {'_id':'unknown'}, 'brief', 'test-1')
    assert result['status'] == 'insufficient_evidence'
    assert result['claims'] == [] and fake.calls == 1

@pytest.mark.asyncio
@pytest.mark.parametrize('response', [TimeoutError('private provider body'), {'facts':'malformed'}])
async def test_fact_service_failure_is_not_evidence_of_absence(museum_settings, response):
    museum_settings.museum_answer_policy = 'facts'
    e, store, fake = await engine(museum_settings, [response])
    result = await e.answer('哪里制作？', {'_id':'outage'}, 'brief', 'test-1')
    assert result['status'] == 'service_unavailable'
    assert result['claims'] == [] and fake.calls == 1
    assert 'private provider body' not in result['answer']
    trace = await store.get('museum_traces', result['trace_id'])
    assert trace['attempts'][0]['stage'] == 'fact_selection'
    assert 'error' in trace['attempts'][0]

@pytest.mark.asyncio
async def test_facts_do_not_replace_semantic_verification(museum_settings):
    museum_settings.museum_answer_policy = 'facts'
    facts = {'facts':[{'aspect':'material','scope':'production','source_id':'test-1',
                      'value':'bronze','quote':'Material: bronze.'}]}
    bad = draft(text='这件不是青铜但又是青铜。')
    verdict = {'passed':False, 'issues':['contradiction']}
    e, _, fake = await engine(museum_settings, [facts,bad,verdict,bad,verdict])
    result = await e.answer('是青铜吗？', {'_id':'bad'}, 'brief', 'test-1')
    assert result['status'] == 'verification_failed'
    assert result['claims'] == [] and fake.calls == 5

@pytest.mark.asyncio
async def test_generation_cannot_cite_unselected_source_fragment(museum_settings):
    museum_settings.museum_answer_policy = 'facts'
    facts = {'facts':[{'aspect':'material','scope':'production','source_id':'test-1',
                      'value':'bronze','quote':'Material: bronze.'}]}
    extra = draft(quote='Date: 1884.', text='年代为1884年。')
    e, _, fake = await engine(museum_settings, [facts,extra,extra])
    result = await e.answer('材质是什么？', {'_id':'extra'}, 'brief', 'test-1')
    assert result['status'] == 'verification_failed'
    assert result['claims'] == [] and fake.calls == 3

@pytest.mark.parametrize('policy,version', [
    ('legacy', 'museum-grounded-v5-photo-context'),
    ('repair', 'museum-grounded-v7-focused-repair'),
    ('facts', 'museum-grounded-v9-fact-selection'),
])
def test_health_reports_actual_answer_policy(museum_settings, policy, version):
    assert museum_settings.museum_answer_policy == 'legacy'
    museum_settings.museum_answer_policy = policy
    with TestClient(create_app(museum_settings)) as client:
        health = client.get('/api/museum/health').json()
        assert health['answer_policy'] == policy
        assert health['prompt_version'] == version

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
