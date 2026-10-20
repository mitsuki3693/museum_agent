import hashlib
import json
import uuid
import pytest
from fastapi.testclient import TestClient
from app.museum.api import create_app
from app.museum.config import MuseumSettings


def settings_for(tmp_path, *, bad_hash=False, bad_quote=False, archived=False):
    content = "A marble figure holds a shell. The sculpture was designed for a fountain."
    digest = hashlib.sha256(content.encode()).hexdigest()
    source = {"_id":"guide-1", "title":"Test figure", "content":content,
        "source_url":"https://example.org/audio", "source_hash":digest,
        "status":"active", "license":"test only", "fetched_at":"2026-09-29",
        "source_kind":"official_transcript", "narrator":"Test curator",
        "narrations":{"version":"test-v1", "human_reviewed":False, "source_hash":digest,
            "content_hash":"stale" if bad_hash else digest,
            "styles":{mode:{"draft":{"abstain":False,"claims":[{
                "text":text,"source_id":"guide-1", "quote":"Invented." if bad_quote else content}]},
                "verdict":{"passed":True,"issues":[]}}
                for mode,text in {"brief":"人物拿着贝壳。","deep":"雕像原为喷泉设计。","children":"找找贝壳在哪里？"}.items()}}}
    base = tmp_path/'base.json'; base.write_text('[]', encoding='utf-8')
    private = tmp_path/'private.json'; private.write_text(json.dumps([source]),encoding='utf-8')
    return MuseumSettings(_env_file=None, museum_corpus=base, museum_private_corpus=private,
                          museum_embedding='lexical', deepseek_api_key='')


def session(client):
    return {'Authorization':'Bearer '+client.post('/api/museum/sessions').json()['token']}


@pytest.mark.parametrize('mode', ['brief','deep','children'])
def test_prepared_styles_no_model_and_official_provenance(tmp_path, mode):
    with TestClient(create_app(settings_for(tmp_path))) as client:
        headers=session(client)
        body={'query':'讲解','object_id':'guide-1','mode':mode,'action':'narration','request_id':str(uuid.uuid4())}
        r=client.post('/api/museum/chat',headers=headers,json=body)
        assert r.status_code==200
        result=r.json()
        assert result['status']=='answered' and result['usage']==[]
        assert result['narration']['human_reviewed'] is False
        assert result['mode']==mode and result['sources'][0]['narrator']=='Test curator'
        assert client.post('/api/museum/chat',headers=headers,json=body).json()['trace_id']==result['trace_id']
        body['mode']='deep' if mode!='deep' else 'brief'
        assert client.post('/api/museum/chat',headers=headers,json=body).status_code==409


@pytest.mark.parametrize('fault', ['bad_hash','bad_quote'])
def test_stale_or_forged_prepared_script_is_not_shown(tmp_path, fault):
    with TestClient(create_app(settings_for(tmp_path,**{fault:True}))) as client:
        result=client.post('/api/museum/chat',headers=session(client),json={
            'query':'讲解','object_id':'guide-1','action':'narration','request_id':str(uuid.uuid4())}).json()
        assert result['status']=='retrieval_only' and 'narration' not in result


def test_question_does_not_return_canned_narration_and_requires_selection(tmp_path):
    with TestClient(create_app(settings_for(tmp_path))) as client:
        headers=session(client)
        result=client.post('/api/museum/chat',headers=headers,json={
            'query':'shell','object_id':'guide-1','request_id':str(uuid.uuid4())}).json()
        assert result['status']=='retrieval_only' and 'narration' not in result
        assert client.post('/api/museum/chat',headers=headers,json={
            'query':'讲解','action':'narration','request_id':str(uuid.uuid4())}).status_code==422


def test_private_corpus_opt_in_and_image_traversal_blocked(tmp_path):
    config=settings_for(tmp_path)
    data=json.loads(config.museum_private_corpus.read_text())
    data[0]['local_image']='../outside.jpg'
    config.museum_private_corpus.write_text(json.dumps(data))
    with TestClient(create_app(config)) as client:
        assert client.get('/api/museum/objects/guide-1/image').status_code==404
    assert MuseumSettings(_env_file=None).museum_private_corpus is None


@pytest.mark.parametrize('script_state', ['missing', 'stale'])
def test_confirmed_homonym_narration_does_not_reopen_discovery(tmp_path, script_state):
    config = settings_for(tmp_path, bad_hash=True)
    rows = json.loads(config.museum_private_corpus.read_text())
    rows[0]['title'] = '金字塔形花器'
    if script_state == 'missing':
        rows[0].pop('narrations')
    other = {**rows[0], '_id': 'guide-2'}
    other.pop('narrations', None)
    config.museum_private_corpus.write_text(json.dumps(rows + [other]), encoding='utf-8')
    with TestClient(create_app(config)) as client:
        headers = session(client)
        def ask(query, action='question', object_id=None):
            response = client.post('/api/museum/chat', headers=headers, json={
                'query': query, 'action': action, 'object_id': object_id,
                'request_id': str(uuid.uuid4())})
            assert response.status_code == 200
            return response.json()
        found = ask('金字塔形花器')
        assert {c['id'] for c in found['candidates']} == {'guide-1', 'guide-2'}
        # The actual click path submits the display name AND the chosen ID.
        for chosen in ('guide-1', 'guide-2'):
            result = ask('请简明讲解《金字塔形花器（马尔伯勒纹章款）》。', 'narration', chosen)
            assert result['status'] == 'retrieval_only'
            assert not result.get('candidates')
            assert result['retrieved_ids'] == [chosen]
            assert result['conversation']['selected_object_id'] == chosen
            followup = ask('这个像塔一样的花器到底怎么插花？请解释结构和用途。', object_id=chosen)
            assert followup['retrieved_ids'] == [chosen]
