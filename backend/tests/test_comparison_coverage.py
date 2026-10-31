"""N15 regression: citation repair must not silently discard a compared work."""
import json

import pytest
from pydantic import ValidationError
from app.museum.fact_selection import FactSelection, selection_issues

from tests.test_conversation_routing import make_engine


def test_long_literal_fact_uses_quote_bound_without_weakening_source_check():
    text='A detailed catalogue description. '*20
    fact=dict(aspect='other',scope='other',source_id='a',value=text,quote=text)
    selection=FactSelection(facts=[fact])
    assert selection_issues(selection,[{'_id':'a','content':text}]) == []
    assert selection_issues(selection,[{'_id':'a','content':'Different source'}]) == ['fact_0:quote_not_in_source']
    with pytest.raises(ValidationError):
        FactSelection(facts=[{**fact,'value':'x'*1201,'quote':'x'*1201}])


@pytest.mark.asyncio
@pytest.mark.parametrize('repair', [True, False])
async def test_comparison_cannot_pass_after_dropping_supported_work(tmp_path, repair):
    engine, store = await make_engine(tmp_path)
    engine.settings = engine.settings.model_copy(update={
        'deepseek_api_key': 'fixture', 'museum_answer_policy': 'facts',
        'museum_rewrite_overlong_answers': True})
    claims = [dict(text='这件作品采用大理石。', source_id='old', quote='Materials and techniques: Marble'),
              dict(text='另一件采用上色石膏。', source_id='bust', quote='Materials and techniques: Plaster painted')]

    class Client:
        generations = []
        async def complete_json(self, messages):
            prompt = messages[0]['content']
            payload = json.loads(messages[1]['content'])
            if prompt.startswith('你只提取'):
                return {'facts': [dict(aspect='material', scope='other', source_id=c['source_id'],
                                       value=c['quote'], quote=c['quote']) for c in claims]}
            if prompt.startswith('你是独立事实审查员'):
                # Deliberately over-permissive semantic judge reproduces the live failure.
                return {'passed': True, 'issues': []}
            self.generations.append(payload)
            return {'abstain': False, 'claims': claims if repair and len(self.generations) > 1 else claims[:1]}

    client = Client()
    engine.client_factory = lambda: client
    session = dict(_id='comparison', object_id='old', history=[])
    result = await engine.answer('和编号 C.615-1925 相比，它们的材料有什么不同？', session, 'brief', 'old')
    trace = await store.get('museum_traces', result['trace_id'])
    assert trace['conversation_route']['kind'] == 'comparison'
    assert len(client.generations) == 2
    assert any('comparison_missing_sources' in issue for issue in client.generations[1]['previous_issues'])
    assert result['status'] == ('answered' if repair else 'verification_failed')
    assert session['object_id'] == 'old'
    if repair:
        assert {c['source_id'] for c in result['claims']} == {'old', 'bust'}
    assert client.generations[0]['max_claims'] == 4


@pytest.mark.asyncio
@pytest.mark.parametrize('second_valid', [True, False])
async def test_fact_schema_error_gets_one_bounded_reselection(tmp_path, second_valid):
    engine, store = await make_engine(tmp_path)
    engine.settings = engine.settings.model_copy(update={
        'deepseek_api_key':'fixture', 'museum_answer_policy':'facts'})
    class Client:
        selections = 0
        async def complete_json(self, messages):
            prompt=messages[0]['content']
            if prompt.startswith('你只提取'):
                self.selections += 1
                return {'facts':[dict(aspect='material' if second_valid and self.selections == 2 else 'shape',
                    scope='other',source_id='old',value='Marble',quote='Materials and techniques: Marble')]}
            if prompt.startswith('你是独立事实审查员'):
                return {'passed':True,'issues':[]}
            return {'abstain':False,'claims':[dict(text='大理石。',source_id='old',quote='Materials and techniques: Marble')]}
    client=Client(); engine.client_factory=lambda:client
    result=await engine.answer('说说它的材质',dict(_id='schema',object_id='old',history=[]),'brief','old')
    assert client.selections == 2
    assert result['status'] == ('answered' if second_valid else 'service_unavailable')
    trace=await store.get('museum_traces',result['trace_id'])
    assert any(a.get('issues',[''])[0].startswith('schema:') for a in trace['attempts'] if a.get('issues'))
