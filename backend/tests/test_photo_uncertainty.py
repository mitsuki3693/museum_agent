import hashlib
import io
import json
import uuid

import pytest
from PIL import Image
from fastapi.testclient import TestClient
from app.museum.photo_policy import Comparisons, decide, label_support
from app.museum.api import create_app
from app.museum.config import MuseumSettings

SOURCE = {'_id':'vase','title':'Vase','content':'Blue vase.','source_url':'https://example.org/vase',
          'source_hash':'v1','status':'active','license':'test','fetched_at':'2026-10-01',
          'fields':{'accession_number':'C.615-1925'}}

def comparison(parts=('base','top'), difference=False, identity='same_work'):
    return {'candidate_id':'vase', 'identity':identity, 'shared_features':['blue_white','tiered'], 'needs':['base','label'],
            'features':[{'part':part,'query_detail':'private observation','reference_detail':'reference detail',
                         'relation':'different' if difference and i==0 else 'match','distinctive':True}
                        for i,part in enumerate(parts)]}

def decision(row, refs=True, label=''):
    return decide(Comparisons.model_validate({'comparisons':[row]}), [SOURCE],
                  [{'source_id':'vase','score':.999}] if refs else [], label)

def test_high_similarity_cannot_override_visible_difference():
    result, trace = decision(comparison(difference=True))
    assert result['match_state']=='no_reliable_match' and result['candidates']==[]
    assert result['similar_candidates'][0]['id']=='vase'
    assert result['similar_candidates'][0]['shared_features']==['蓝白装饰','多层塔形结构']
    assert trace[0]['reason']=='visible_difference'
    assert 'private observation' not in json.dumps(trace)
    assert '.999' not in json.dumps(result) and not result['identity_confirmed']

@pytest.mark.parametrize('parts,refs,label,state',[
    (('base','top'),True,'','likely_match'),
    (('base',),True,'','uncertain'),
    (('base','base'),True,'','uncertain'),
    (('base','top'),False,'','no_reliable_match'),
    ((),True,'','no_reliable_match'),
    ((),False,'C.615-1925','likely_match'),
])
def test_tiers_require_identity_evidence_not_a_model_claim(parts,refs,label,state):
    result,_=decision(comparison(parts),refs,label)
    assert result['match_state']==state and result['confirmation_required']

def test_generic_similarity_has_no_identification_candidate():
    row=comparison()
    for f in row['features']:f['distinctive']=False
    result,_=decision(row)
    assert not result['candidates'] and len(result['similar_candidates'])==1


def test_known_lookalikes_require_label_even_with_high_similarity_and_claimed_details():
    rows=Comparisons.model_validate({'comparisons':[comparison()]})
    hits=[{'source_id':'vase','score':.999}]
    result,trace=decide(rows,[SOURCE],hits,'',{'vase'})
    assert result['match_state']=='no_reliable_match' and not result['candidates']
    assert result['similar_candidates'][0]['id']=='vase'
    assert trace[0]['reason']=='lookalike_requires_label'
    supported,_=decide(rows,[SOURCE],hits,'展签 C.615-1925',{'vase'})
    assert supported['match_state']=='likely_match' and supported['confirmation_required']
    different,_=decide(Comparisons.model_validate({'comparisons':[comparison(difference=True)]}),[SOURCE],hits,'C.615-1925',{'vase'})
    assert different['match_state']=='no_reliable_match'

def test_accession_requires_boundaries_not_prefix_or_suffix():
    assert label_support(SOURCE,'展签 C.615-1925 名称')
    assert label_support(SOURCE,'c 615 1925')
    assert not label_support(SOURCE,'C.615-19250')
    assert not label_support(SOURCE,'XC.615-1925')

def test_unknown_and_duplicate_ids_fail_closed():
    for rows in [[{**comparison(),'candidate_id':'invented'}],[comparison(),comparison()]]:
        with pytest.raises(ValueError):
            decide(Comparisons.model_validate({'comparisons':rows}),[SOURCE],[], '')

def png():
    out=io.BytesIO();Image.new('RGB',(24,32),'blue').save(out,format='PNG');return out.getvalue()

@pytest.fixture
def api(tmp_path):
    path=tmp_path/'corpus.json';path.write_text(json.dumps([SOURCE]),encoding='utf-8')
    class Client:
        async def complete_json(self,messages):
            if '观察这张照片' in str(messages):
                return {'usable':True,'visible_text':'','visual_description':'Blue vase'}
            return {'comparisons':[comparison(parts=(),identity='uncertain')]}
    config=MuseumSettings(_env_file=None,museum_corpus=path,museum_embedding='lexical',
                          museum_storage='memory',deepseek_api_key='test',museum_admin_token='review')
    with TestClient(create_app(config,client_factory=Client)) as client:yield client

def session(client):
    token=client.post('/api/museum/sessions').json()['token']
    return {'Authorization':'Bearer '+token},hashlib.sha256(token.encode()).hexdigest()

def photo(client,headers,parent=None):
    return client.post('/api/museum/recognize',headers=headers,files={'photo':('x.png',png(),'image/png')},
                       data={'parent_trace_id':parent} if parent else {})

def test_similar_browse_cannot_be_forged_into_confirmation_and_is_session_scoped(api):
    a,sid=session(api);b,_=session(api)
    result=photo(api,a).json();tid=result['trace_id']
    assert result['match_state']=='no_reliable_match' and result['similar_candidates']
    url='/api/museum/photo-actions'
    assert api.post(url,headers=b,json={'trace_id':tid,'action':'view_similar','object_id':'vase'}).status_code==404
    assert api.post(url,headers=a,json={'trace_id':tid,'action':'confirm','object_id':'vase'}).status_code==422
    assert api.post(url,headers=a,json={'trace_id':tid,'action':'view_similar','object_id':'wrong'}).status_code==422
    for _ in range(2):
        assert api.post(url,headers=a,json={'trace_id':tid,'action':'view_similar','object_id':'vase'}).status_code==200
    trace=api.portal.call(api.app.state.store.get,'museum_photo_traces',tid)
    assert len(trace['interactions'])==1 and not trace.get('user_confirmed_object_id')
    assert trace['identity_confirmed'] is False
    current=api.portal.call(api.app.state.store.get,'museum_sessions',sid)
    assert current['photo_selection']['action']=='view_similar'
    # A prepared catalogue narration is still a catalogue view, not photo confirmation.
    async def narration(query,current,mode,object_id):
        result={'trace_id':current['_trace_id'],'status':'answered','claims':[], 'answer':'catalogue','sources':[]}
        await api.app.state.store.upsert('museum_traces',{'_id':result['trace_id'],'session_id':sid,'created_at':1,'result':result})
        return result
    api.app.state.engine.narrate=narration
    answer=api.post('/api/museum/chat',headers=a,json={'query':'介绍这件相似馆藏','action':'narration','object_id':'vase','request_id':str(uuid.uuid4())}).json()
    assert '不代表已确认' in answer['context_notice']
    assert answer['photo_selection']['action']=='view_similar'
    assert photo(api,b,parent=tid).status_code==404
    child=photo(api,a,parent=tid).json()
    stored=api.portal.call(api.app.state.store.get,'museum_photo_traces',child['trace_id'])
    assert stored['parent_photo_trace_id']==tid
    current=api.portal.call(api.app.state.store.get,'museum_sessions',sid)
    assert not current.get('photo_selection') and not current.get('object_id')

def test_confirm_only_records_user_assertion_and_retry_preserves_trace(api):
    a,sid=session(api)
    tid=photo(api,a).json()['trace_id']
    store=api.app.state.store
    trace=api.portal.call(store.get,'museum_photo_traces',tid)
    trace.update(candidate_ids=['vase'],similar_candidate_ids=[],match_state='likely_match')
    api.portal.call(store.upsert,'museum_photo_traces',trace)
    assert api.post('/api/museum/photo-actions',headers=a,json={'trace_id':tid,'action':'confirm','object_id':'vase'}).status_code==200
    trace=api.portal.call(store.get,'museum_photo_traces',tid)
    assert trace['user_confirmed_object_id']=='vase' and trace['identity_confirmed'] is False
    assert api.post('/api/museum/photo-actions',headers=a,json={'trace_id':tid,'action':'retry'}).status_code==200
    current=api.portal.call(store.get,'museum_sessions',sid)
    assert not current.get('photo_selection') and not current.get('object_id')
    assert api.portal.call(store.get,'museum_photo_traces',tid) is not None


def test_two_candidate_display_keeps_all_comparisons_for_review():
    sources = [{**SOURCE, '_id': name} for name in ('a', 'b', 'c')]
    rows = [{**comparison(), 'candidate_id': name} for name in ('a', 'b', 'c')]
    hits = [{'source_id': name, 'score': score} for name, score in [('a', .7), ('b', .9), ('c', .8)]]
    result, audit = decide(Comparisons.model_validate({'comparisons': rows}), sources, hits, '')
    assert [c['id'] for c in result['candidates']] == ['b', 'c']
    assert result['match_state'] == 'uncertain'
    assert len(audit) == 3 and result['identity_confirmed'] is False


def test_retake_once_then_alternatives_are_session_scoped_and_persisted(api):
    headers, sid = session(api)
    other, _ = session(api)
    first = photo(api, headers).json()
    assert first['retake_count'] == 0
    child = photo(api, headers, first['trace_id']).json()
    assert child['retake_count'] == 1
    body = {'trace_id': child['trace_id'], 'action': 'retry'}
    assert api.post('/api/museum/photo-actions', headers=headers, json=body).status_code == 409
    assert photo(api, headers, child['trace_id']).status_code == 409
    for action in ('reject', 'search', 'browse'):
        body['action'] = action
        assert api.post('/api/museum/photo-actions', headers=other, json=body).status_code == 404
        assert api.post('/api/museum/photo-actions', headers=headers, json={**body, 'object_id': 'vase'}).status_code == 422
        assert api.post('/api/museum/photo-actions', headers=headers, json=body).status_code == 200
    trace = api.portal.call(api.app.state.store.get, 'museum_photo_traces', child['trace_id'])
    assert trace['retake_count'] == 1
    assert [e['action'] for e in trace['interactions']] == ['reject', 'search', 'browse']
    assert not trace.get('user_confirmed_object_id') and not trace['identity_confirmed']
    current = api.portal.call(api.app.state.store.get, 'museum_sessions', sid)
    assert not current.get('object_id') and not current.get('photo_selection')
    # A different artwork is a new task, not permanently blocked by an earlier retake.
    assert photo(api, headers).json()['retake_count'] == 0


def test_service_failure_does_not_consume_another_retake(api):
    headers, _ = session(api)
    original = api.app.state.engine.client_factory
    first = photo(api, headers).json()
    class Unavailable:
        async def complete_json(self, messages):
            raise ConnectionError('offline test')
    api.app.state.engine.client_factory = Unavailable
    failed = photo(api, headers, first['trace_id']).json()
    assert failed['status'] == 'service_unavailable' and failed['retake_count'] == 1
    assert api.post('/api/museum/photo-actions', headers=headers, json={
        'trace_id': failed['trace_id'], 'action': 'retry'}).status_code == 200
    api.app.state.engine.client_factory = original
    completed = photo(api, headers, failed['trace_id']).json()
    assert completed['retake_count'] == 1 and completed['status'] != 'service_unavailable'
