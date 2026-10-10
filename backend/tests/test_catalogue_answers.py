import hashlib
import json

import pytest

from app.museum.config import MuseumSettings
from app.museum.engine import MuseumEngine
from app.museum.retrieval import MuseumIndex
from app.storage.store import MemoryStore


class CountingClient:
    def __init__(self):
        self.calls = 0

    async def complete_json(self, messages):
        self.calls += 1
        raise RuntimeError('A catalogue field should not require model inference')


async def setup_engine(tmp_path, enabled=True):
    content = 'Title: Test object\nMaker: Example workshop (attributed to)\nDate: ca. 1695\nMaterials and techniques: Tin-glazed earthenware, painted in blue'
    record = dict(_id='test-1', title='Test object', content=content,
        source_hash=hashlib.sha256(content.encode()).hexdigest(), status='active',
        source_url='https://example.org/object', license='CC0', fetched_at='2026-10-10')
    path = tmp_path/'corpus.json'
    path.write_text(json.dumps([record]), encoding='utf-8')
    cfg = MuseumSettings(_env_file=None, museum_corpus=path, museum_embedding='lexical',
        deepseek_api_key='test-only', museum_answer_policy='facts', museum_catalogue_answers=enabled)
    store = MemoryStore()
    index = MuseumIndex(cfg, store)
    await index.start()
    client = CountingClient()
    return MuseumEngine(cfg, store, index, lambda:client), store, client, record


@pytest.mark.asyncio
async def test_selected_catalogue_field_avoids_all_model_calls(tmp_path):
    engine, store, client, record = await setup_engine(tmp_path)
    session = dict(_id='s', object_id='test-1', history=[{'role':'user','content':'介绍这件作品'}])
    result = await engine.answer('它是什么材质？', session, 'brief', None)
    assert client.calls == 0
    assert result['status'] == 'answered'
    assert result['claims'][0]['quote'] in record['content']
    assert 'Tin-glazed earthenware, painted in blue' in result['answer']
    trace = await store.get('museum_traces', result['trace_id'])
    assert trace['answer_route'] == 'catalogue_field'
    assert trace['retrieval_route'] == 'selected_object'


@pytest.mark.asyncio
@pytest.mark.parametrize('query,expected', [
    ('谁制作的？','Example workshop (attributed to)'),
    ('它是什么年代的？','ca. 1695'),
    ('What is it made of?','Tin-glazed earthenware, painted in blue'),
])
async def test_original_qualifiers_and_full_value_are_preserved(tmp_path, query, expected):
    engine, store, client, _ = await setup_engine(tmp_path)
    result = await engine.answer(query, {'_id':'s'}, 'children', 'test-1')
    assert result['answer'].endswith(expected) and client.calls == 0
    assert result['verification'] == dict(passed=True,kind='exact_catalogue_field')
    trace=await store.get('museum_traces',result['trace_id'])
    assert trace['timing']['calls']['catalogue']==1 and trace['timing']['calls']['generation']==0


@pytest.mark.asyncio
@pytest.mark.parametrize('query', [
    '它不是青铜的吗？','它是什么材质，为什么选择这种材料？',
    '作者是谁？他吃过什么早餐？','另一个作品的作者是谁？',
    '比较这两件作品的年代','这个仿品是什么年代的？','Who made this and why?',
])
async def test_premises_multiple_intents_and_other_objects_keep_model_path(tmp_path, query):
    engine, store, client, _ = await setup_engine(tmp_path)
    result=await engine.answer(query,{'_id':'s'},'brief','test-1')
    trace=await store.get('museum_traces',result['trace_id'])
    assert trace['answer_route']=='model_or_discovery' and client.calls>0


@pytest.mark.asyncio
@pytest.mark.parametrize('action', ['view_similar', 'view_number'])
async def test_viewing_similar_object_is_not_photo_confirmation(tmp_path, action):
    engine, store, client, _ = await setup_engine(tmp_path)
    session=dict(_id='s',object_id='test-1',photo_selection=dict(action=action,object_id='test-1'))
    result=await engine.answer('作者是谁？',session,'brief',None)
    assert client.calls>0
    trace=await store.get('museum_traces',result['trace_id'])
    assert trace['answer_route']!='catalogue_field'


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['hash','content','inactive','missing','duplicate'])
async def test_stale_or_ambiguous_fields_do_not_get_fast_answer(tmp_path, fault):
    from copy import deepcopy
    engine, store, client, record = await setup_engine(tmp_path)
    modified=deepcopy(record)
    if fault=='hash': modified['source_hash']='stale'
    elif fault=='content': modified['content']=modified['content'].replace('ca. 1695','1900')
    elif fault=='inactive': modified['status']='archived'
    else:
        modified['content']=modified['content'].replace('Date: ca. 1695','Date: ' if fault=='missing' else 'Date: ca. 1695\nDate: 1900')
        engine.index.records['test-1']=deepcopy(modified)
    await store.upsert('museum_sources',modified)
    result=await engine.answer('它是什么年代？',{'_id':'s'},'brief','test-1')
    trace=await store.get('museum_traces',result['trace_id'])
    assert trace['answer_route']!='catalogue_field'


@pytest.mark.asyncio
async def test_missing_selection_and_disabled_flag_do_not_fast_answer(tmp_path):
    engine, store, client, _=await setup_engine(tmp_path, enabled=False)
    result=await engine.answer('作者是谁？',{'_id':'s'},'brief','test-1')
    assert client.calls>0
    engine.settings.museum_catalogue_answers=True
    client.calls=0
    result=await engine.answer('作者是谁？',{'_id':'s'},'brief',None)
    trace=await store.get('museum_traces',result['trace_id'])
    assert trace['answer_route']!='catalogue_field' and result['status']!='answered'


@pytest.mark.asyncio
async def test_open_question_still_requires_verification_after_repair(tmp_path):
    engine, store, client, record=await setup_engine(tmp_path)
    quote=record['content'].splitlines()[-1]
    draft=dict(abstain=False,claims=[dict(text='馆方编目写明它是蓝绘锡釉陶器。',source_id='test-1',quote=quote)])
    responses=iter([dict(facts=[dict(aspect='material',scope='production',source_id='test-1',value='Tin-glazed earthenware',quote=quote)]),
        draft,dict(passed=False,issues=['unsupported explanation']),draft,dict(passed=True,issues=[])])
    async def respond(messages):
        client.calls+=1
        return next(responses)
    client.complete_json=respond
    result=await engine.answer('这种材料有什么特点？',{'_id':'s'},'brief','test-1')
    assert client.calls==5 and result['status']=='answered'
    trace=await store.get('museum_traces',result['trace_id'])
    assert trace['timing']['calls']['generation']==2
    assert trace['timing']['calls']['verification']==2
    assert trace['timing']['calls']['fact_selection']==1


@pytest.mark.asyncio
async def test_bulk_import_material_label_is_literal_evidence(tmp_path):
    from copy import deepcopy
    engine, store, client, record=await setup_engine(tmp_path)
    record['content']=record['content'].replace('Materials and techniques:', 'materialsAndTechniques:')
    await store.upsert('museum_sources',record)
    engine.index.records['test-1']=deepcopy(record)
    result=await engine.answer('材质是什么？',{'_id':'s'},'brief','test-1')
    assert client.calls==0 and result['claims'][0]['quote'].startswith('materialsAndTechniques:')
