"""An OCR number can offer catalogue browsing, never override visual identity."""
import json
import uuid

import pytest
from fastapi.testclient import TestClient

from app.museum.api import create_app
from app.museum.config import MuseumSettings
from app.museum.photo_policy import Comparisons, decide
from .test_photo_uncertainty import SOURCE, comparison, photo, session


def test_rear_mark_front_decoration_conflict_keeps_number_as_browse_only():
    from app.museum.photo_policy import add_number_clues
    evidence = comparison(difference=True, identity='different_work')
    result, audit = decide(Comparisons.model_validate({'comparisons':[evidence]}), [SOURCE],
                          [{'source_id':'vase','score':.9}], 'C.615-1925')
    add_number_clues(result, [SOURCE], ['vase'])
    assert result['candidates'] == [] and result['identity_confirmed'] is False
    assert result['match_state'] == 'no_reliable_match'
    assert result['number_candidates'][0]['id'] == 'vase'
    assert audit[0]['reason'] == 'visible_difference'  # conflict retained, not erased


@pytest.mark.parametrize('exact,status,selected,expected', [
    ([], 'not_matched', [], []), (['unknown'], 'not_matched', [], []),
    (['vase'], 'service_unavailable', [], []), (['vase'], 'needs_confirmation', [{'id':'vase'}], []),
    (['vase'], 'not_matched', [], ['vase']),
])
def test_clues_require_current_exact_route_and_do_not_duplicate(exact,status,selected,expected):
    from app.museum.photo_policy import add_number_clues
    result = dict(status=status, candidates=selected, identity_confirmed=False)
    add_number_clues(result, [SOURCE], exact)
    assert [c['id'] for c in result['number_candidates']] == expected
    assert result['identity_confirmed'] is False


def test_ambiguous_number_is_bounded_without_inventing_a_single_identity():
    from app.museum.photo_policy import add_number_clues
    sources = [{**SOURCE,'_id':str(n)} for n in range(3)]
    result = dict(status='not_matched',candidates=[],identity_confirmed=False)
    add_number_clues(result,sources,['0','0','1','2'])
    assert [c['id'] for c in result['number_candidates']] == ['0','1']


@pytest.fixture
def api(tmp_path):
    path=tmp_path/'corpus.json'; path.write_text(json.dumps([SOURCE]),encoding='utf-8')
    class Client:
        async def complete_json(self,messages):
            if messages[0]['content'].startswith('只描述照片'):
                return dict(usable=True,visible_text='C.615-1925',visual_description='underside mark')
            return {'comparisons':[comparison(difference=True,identity='different_work')]}
    cfg=MuseumSettings(_env_file=None,museum_corpus=path,museum_embedding='lexical',
        museum_storage='memory',deepseek_api_key='test',museum_admin_token='review')
    with TestClient(create_app(cfg,client_factory=Client)) as client:
        yield client


def test_number_browsing_is_session_scoped_and_cannot_confirm_identity(api):
    a,sid=session(api); b,_=session(api)
    result=photo(api,a).json(); tid=result['trace_id']
    assert [c['id'] for c in result['number_candidates']] == ['vase']
    url='/api/museum/photo-actions'
    body=dict(trace_id=tid,action='view_number',object_id='vase')
    assert api.post(url,headers=b,json=body).status_code==404
    assert api.post(url,headers=a,json={**body,'object_id':'wrong'}).status_code==422
    assert api.post(url,headers=a,json={**body,'action':'confirm'}).status_code==422
    for _ in range(2):
        assert api.post(url,headers=a,json=body).status_code==200
    trace=api.portal.call(api.app.state.store.get,'museum_photo_traces',tid)
    assert trace['number_candidate_ids']==['vase'] and trace['candidate_ids']==[]
    assert len(trace['interactions'])==1 and 'user_confirmed_object_id' not in trace
    assert trace['identity_confirmed'] is False
    async def narration(query,current,mode,object_id):
        result={'trace_id':current['_trace_id'],'status':'answered','claims':[],'answer':'catalogue','sources':[]}
        await api.app.state.store.upsert('museum_traces',{'_id':result['trace_id'],'session_id':sid,'created_at':1,'result':result})
        return result
    api.app.state.engine.narrate=narration
    response=api.post('/api/museum/chat',headers=a,json=dict(query='介绍这件馆藏',action='narration',object_id='vase',request_id=str(uuid.uuid4())))
    assert response.status_code==200
    assert '编号' in response.json()['context_notice'] and '不代表已确认' in response.json()['context_notice']
    assert response.json()['photo_selection']['action']=='view_number'
