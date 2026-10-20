"""Visitor task regressions: actual engine, local corpus, no paid model."""
import hashlib
import json

import pytest

from app.museum.config import MuseumSettings
from app.museum.engine import MuseumEngine
from app.museum.retrieval import MuseumIndex
from app.storage.store import MemoryStore


async def make_engine(tmp_path):
    rows = []
    for sid, title, number, material in [
        ('old', 'Neptune and Triton', 'A.1-1900', 'Marble'),
        ('bust', 'Bust of a woman', 'C.615-1925', 'Plaster painted'),
        ('table1', 'Table', 'W.1-1900', 'Wood'),
        ('table2', 'Table', 'W.2-1900', 'Oak'),
        ('chair', "Child’s chair", 'W.3-1900', 'Wood'),
    ]:
        content = f'Title: {title}\nMuseum number: {number}\nMaterials and techniques: {material}'
        rows.append(dict(_id=sid, title=title, content=content, status='active',
            fields={'accession_number': number}, source_hash=hashlib.sha256(content.encode()).hexdigest(),
            source_url=f'https://example.org/{sid}', license='fixture', fetched_at='2026-10-20'))
    path = tmp_path/'corpus.json'
    path.write_text(json.dumps(rows), encoding='utf-8')
    cfg = MuseumSettings(_env_file=None, museum_corpus=path, museum_embedding='lexical',
                         deepseek_api_key='', museum_catalogue_answers=True)
    store = MemoryStore()
    index = MuseumIndex(cfg, store)
    await index.start()
    return MuseumEngine(cfg, store, index), store


def old_session():
    return dict(_id='session', object_id='old', history=[
        dict(role='user', content='介绍海神'), dict(role='assistant', content='这是海神与特里同。')],
        photo_selection=dict(action='view_similar', object_id='old'))


@pytest.mark.asyncio
@pytest.mark.parametrize('query', ['帮我找 c.615-1925', '请介绍 C 615 1925。', '看看Ｃ．６１５－１９２５'])
async def test_sentence_accession_replaces_old_constraint(tmp_path, query):
    engine, store = await make_engine(tmp_path)
    session = old_session()
    result = await engine.answer(query, session, 'brief', 'old')
    assert result['status'] == 'needs_confirmation'
    assert [c['id'] for c in result['candidates']] == ['bust']
    assert session.get('object_id') is None and 'photo_selection' not in session
    trace = await store.get('museum_traces', result['trace_id'])
    assert trace['conversation_route']['kind'] == 'accession'
    assert trace['object_id'] is None and trace['rewrite_error'] is None


@pytest.mark.asyncio
async def test_broad_title_keeps_grounded_homonyms_without_model(tmp_path):
    engine, _ = await make_engine(tmp_path)
    session = old_session()
    result = await engine.answer('Table', session, 'brief', 'old')
    assert result['status'] == 'needs_confirmation'
    assert {c['id'] for c in result['candidates']} == {'table1', 'table2'}
    assert session.get('object_id') is None


@pytest.mark.asyncio
async def test_new_name_avoids_rewrite_then_selected_followup_uses_new_work(tmp_path):
    engine, store = await make_engine(tmp_path)
    session = old_session()
    result = await engine.answer('换一件，找 Bust of a woman', session, 'brief', 'old')
    assert [c['id'] for c in result['candidates']] == ['bust']
    result = await engine.answer('它是什么材质？', session, 'brief', 'bust')
    assert result['status'] == 'answered'
    assert '彩绘石膏（Plaster painted）' in result['answer']
    assert session['object_id'] == 'bust'
    assert result['claims'][0]['quote'] == 'Materials and techniques: Plaster painted'


@pytest.mark.asyncio
async def test_comparison_keeps_current_object_and_both_sources(tmp_path):
    engine, store = await make_engine(tmp_path)
    session = old_session()
    result = await engine.answer('这件和 C.615-1925 有什么不同？', session, 'brief', 'old')
    assert session['object_id'] == 'old'
    assert session['photo_selection']['action'] == 'view_similar'
    assert set(result['retrieved_ids']) == {'old', 'bust'}
    trace = await store.get('museum_traces', result['trace_id'])
    assert trace['conversation_route']['kind'] == 'comparison'


@pytest.mark.asyncio
async def test_named_comparison_preserves_selection_and_adds_comparison_target(tmp_path):
    engine, _ = await make_engine(tmp_path)
    session = dict(_id='s', object_id='bust', history=[])
    result = await engine.answer('这件和 Neptune and Triton 有什么不同？', session, 'brief', 'bust')
    assert session['object_id'] == 'bust'
    assert set(result['retrieved_ids']) == {'bust', 'old'}


@pytest.mark.asyncio
async def test_two_named_comparison_targets_without_selection(tmp_path):
    engine, _ = await make_engine(tmp_path)
    session = dict(_id='s', history=[])
    result = await engine.answer('比较 Neptune and Triton 与 Bust of a woman', session, 'brief', '')
    assert session.get('object_id') is None
    assert set(result['retrieved_ids']) == {'bust', 'old'}


@pytest.mark.asyncio
async def test_prepared_narration_switch_clears_previous_work_before_followup(tmp_path):
    engine, store = await make_engine(tmp_path)
    source = await store.get('museum_sources', 'bust')
    source['narrations'] = {
        'version': 'fixture-v1', 'source_hash': source['source_hash'],
        'content_hash': hashlib.sha256(source['content'].encode()).hexdigest(),
        'styles': {'brief': {
            'draft': {'abstain': False, 'claims': [{
                'text': '这是女性半身像。', 'source_id': 'bust', 'quote': 'Title: Bust of a woman'}]},
            'verdict': {'passed': True, 'issues': []}}}}
    await store.upsert('museum_sources', source)
    session = old_session()
    result = await engine.narrate('简单介绍一下', session, 'brief', 'bust')
    assert result['narration']['prepared']
    assert session['object_id'] == 'bust'
    assert len(session['history']) == 2 and '海神' not in str(session['history'])
    assert 'photo_selection' not in session
    assert result['conversation']['selection_reset']
    class Fake:
        calls = []
        async def complete_json(self, messages):
            self.calls.append(messages)
            return {'query': 'Bust of a woman usage'}
    client = Fake()
    engine.client_factory = lambda: client
    engine.settings.deepseek_api_key = 'fixture-only'
    await engine.answer('它的用途是什么？', session, 'brief', 'bust')
    assert '女性半身像' in client.calls[0][1]['content']
    assert '海神' not in client.calls[0][1]['content']


@pytest.mark.asyncio
async def test_explicit_switch_without_target_asks_for_target(tmp_path):
    engine, _ = await make_engine(tmp_path)
    session = old_session()
    result = await engine.answer('换一件', session, 'brief', 'old')
    assert session.get('object_id') is None
    assert result['status'] == 'needs_clarification'
    assert not result.get('candidates')


@pytest.mark.asyncio
async def test_followup_preserves_context_and_photo_identity_boundary(tmp_path):
    engine, store = await make_engine(tmp_path)
    session = old_session()
    result = await engine.answer('它是什么材质？', session, 'brief', None)
    assert session['object_id'] == 'old'
    assert result['retrieved_ids'] == ['old']
    trace = await store.get('museum_traces', result['trace_id'])
    assert trace['answer_route'] != 'catalogue_field'


@pytest.mark.asyncio
async def test_sentence_accession_field_answers_specific_question(tmp_path):
    engine, _ = await make_engine(tmp_path)
    session = old_session()
    result = await engine.answer('请问 c.615-1925 是什么材质？', session, 'brief', 'old')
    assert result['status'] == 'answered'
    assert '彩绘石膏（Plaster painted）' in result['answer']
    assert session['object_id'] == 'bust'
    assert result['conversation']['selected_object_id'] == 'bust'


@pytest.mark.asyncio
async def test_changing_style_does_not_clear_selection(tmp_path):
    engine, _ = await make_engine(tmp_path)
    session = old_session()
    result = await engine.answer('换成给8岁孩子听的说法。', session, 'brief', 'old')
    assert result['retrieved_ids'] == ['old'] and session['object_id'] == 'old'


@pytest.mark.asyncio
async def test_explicit_deselect_cannot_restore_previous_object(tmp_path):
    engine, _ = await make_engine(tmp_path)
    session = old_session()
    result = await engine.answer('都不是，我再补充一下', session, 'brief', '')
    assert session.get('object_id') is None
    assert result['conversation']['selected_object_id'] is None


@pytest.mark.asyncio
async def test_correction_starts_global_search_before_rewrite(tmp_path):
    engine, store = await make_engine(tmp_path)
    session = old_session()
    await engine.answer('我刚才说错了，我要问的是那把像字母m的红色儿童椅。', session, 'brief', 'old')
    assert session.get('object_id') is None
    traces = await store.find('museum_traces', {})
    assert traces[0]['conversation_route']['kind'] == 'search'
    assert not traces[0]['conversation_route']['rewrite']


@pytest.mark.asyncio
@pytest.mark.parametrize('query', ['C.9999-1925是什么材质？', 'C.615-1925-A是什么材质？'])
async def test_unknown_full_number_never_borrows_known_or_previous_work(tmp_path, query):
    engine, _ = await make_engine(tmp_path)
    session = old_session()
    result = await engine.answer(query, session, 'brief', 'old')
    assert result['status'] == 'needs_clarification'
    assert result['retrieved_ids'] == [] and result['claims'] == []
    assert session.get('object_id') is None


@pytest.mark.asyncio
@pytest.mark.parametrize('query', [
    '请讲给一个8岁的小朋友听，最后给他一个能对着作品找答案的小问题。',
    '为什么馆方说这件静止的雕塑很有动感？请用两个具体细节解释。',
    '他和另一个人物是什么关系？',
    '这个带鹿的杯子为什么被叫作谜题酒杯？请简单讲明白。',
])
async def test_incidental_search_words_and_generic_titles_do_not_switch(tmp_path, query):
    engine, _ = await make_engine(tmp_path)
    from app.museum.conversation_routing import ConversationRouter
    engine.index.conversation_router = ConversationRouter(
        engine.index.records, engine.index.accessions,
        {'bust': {'aliases_zh': '雕塑 杯子 花器'}})
    session = old_session()
    result = await engine.answer(query, session, 'brief', 'old')
    assert session['object_id'] == 'old'
    assert result['retrieved_ids'] == ['old']


@pytest.mark.asyncio
async def test_positive_correction_after_rejecting_old_work(tmp_path):
    engine, _ = await make_engine(tmp_path)
    session = old_session()
    result = await engine.answer('不是这件，我要看Bust of a woman', session, 'brief', 'old')
    assert [c['id'] for c in result['candidates']] == ['bust']
    assert session.get('object_id') is None


@pytest.mark.asyncio
@pytest.mark.parametrize('query', ['不要讲女人半身像了，换成海神与特里同', '换成海神与特里同给孩子讲的说法'])
async def test_explicit_switch_to_unindexed_name_still_searches_globally(tmp_path, query):
    engine, store = await make_engine(tmp_path)
    session = old_session()
    result = await engine.answer(query, session, 'brief', 'old')
    assert result['conversation']['selection_reset']
    assert session.get('object_id') is None


@pytest.mark.asyncio
async def test_browser_empty_selection_preserves_unconfirmed_refinement(tmp_path):
    engine, store = await make_engine(tmp_path)
    session = dict(_id='s', history=[{'role':'user','content':'找一个花瓶'},
                                    {'role':'assistant','content':'你找的是这件吗？'}])
    class Fake:
        calls = []
        async def complete_json(self, messages):
            self.calls.append(messages)
            return {'query': 'bronze vase'} if len(self.calls) == 1 else {'intent':'find_artwork','candidate_ids':[]}
    client = Fake()
    engine.client_factory = lambda: client
    engine.settings.deepseek_api_key = 'fixture-only'
    await engine.answer('不是，青铜的', session, 'brief', '')
    assert '找一个花瓶' in client.calls[0][1]['content']
    assert session.get('object_id') is None


@pytest.mark.asyncio
async def test_api_replays_authoritative_state_and_keeps_private_initial_context(tmp_path):
    from fastapi.testclient import TestClient
    from app.museum.api import create_app
    from uuid import uuid4
    engine, _ = await make_engine(tmp_path)
    cfg = engine.settings
    cfg.museum_storage = 'memory'
    with TestClient(create_app(cfg)) as client:
        token = client.post('/api/museum/sessions').json()['token']
        headers = {'Authorization': 'Bearer ' + token}
        def ask(query, object_id):
            body = {'query':query,'object_id':object_id,'request_id':str(uuid4())}
            response = client.post('/api/museum/chat', headers=headers, json=body)
            assert response.status_code == 200
            return response.json(), body
        answer, _ = ask('它是什么材质？', 'old')
        assert answer['conversation']['selected_object_id'] == 'old'
        answer, body = ask('帮我找 c.615-1925', 'old')
        assert answer['conversation']['selected_object_id'] is None
        assert answer['conversation']['selection_reset']
        replay = client.post('/api/museum/chat', headers=headers, json=body).json()
        assert replay['conversation'] == answer['conversation']
        answer, _ = ask('它是什么材质？', 'bust')
        assert answer['conversation']['selected_object_id'] == 'bust'
        assert '彩绘石膏' in answer['answer']
        assert 'initial_context' not in answer
