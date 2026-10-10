import json

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from app.museum.api import create_app
from app.museum.config import MuseumSettings
from app.museum.eval_dashboard import EvalDisplay, present_run, evaluation_dashboard
from app.museum.runtime import RuntimeStore
from app.storage.store import MemoryStore


def run(**fields):
    return dict(_id='test', created_at=1, kind='text', status='completed', dataset_version='frozen-v1',
                dataset_hash='a'*64, corpus_hash='b'*64, model='fixture', prompt_version='v1', **fields)


def test_paired_shard_counts_misses_but_excludes_explicitly_unscored_rows():
    data = run(results=[{'before_rank': 1, 'after_rank': None},
                        {'before_rank': 8, 'after_rank': 3},
                        {'before_rank': 1, 'after_rank': 1, 'scored': False}],
               summary={'groups': {'fake_whole_run': {'n': 114, 'after_top5': 114}}})
    result = present_run(data)
    m = next(m for m in result['metrics'] if m['variant']=='改动后' and m['label']=='Top5 命中')
    assert (m['numerator'], m['denominator']) == (1, 2)
    assert result['decision']=='pending' and result['human_grades_completed']==0
    assert not result['human_reviewed'] and not result['current_deployment_inferred']


def test_missing_and_zero_human_denominators_are_not_perfect_scores():
    result = present_run(run(human_reviewed=True, results=[{'case_id':'a','variant':'v1','human_grade':
        {'facts_correct':0,'facts_total':0,'citations_supported':0,'citations_total':0}}, {'case_id':'b','variant':'v1'}]))
    assert result['human_grades_completed']==1 and result['result_count']==2
    assert all(m['value'] is None for m in result['metrics'])


@pytest.mark.parametrize('metric', [dict(value=1.1), dict(value=float('nan')),
    dict(value=.5,numerator=2,denominator=2), dict(value=0,numerator=0,denominator=0)])
def test_invalid_published_metrics_are_rejected(metric):
    with pytest.raises(ValidationError):
        EvalDisplay(track='route', scope='Simulated route only', metrics=[dict(
            variant='v1',label='task completion',unit='ratio',**metric)])


def test_dashboard_auth_order_empty_tracks_and_projection(tmp_path):
    corpus=tmp_path/'corpus.json';corpus.write_text(json.dumps([{'_id':'vase','title':'Vase',
        'content':'Material: bronze.','status':'active','source_hash':'fixture',
        'source_url':'https://example.org/vase','license':'CC0','fetched_at':'2026-10-10'}]))
    cfg=MuseumSettings(_env_file=None,museum_corpus=corpus,museum_embedding='lexical',museum_admin_token='test-review')
    with TestClient(create_app(cfg)) as c:
        headers={'Authorization':'Bearer test-review'}
        assert c.get('/api/museum/admin/evaluations').status_code==403
        visitor=c.post('/api/museum/sessions').json()['token']
        assert c.get('/api/museum/admin/evaluations',headers={'Authorization':'Bearer '+visitor}).status_code==403
        for i in [1,3,2]:
            data=run(results=[{'private':'DO NOT PROJECT'}]);data.update(_id=str(i),created_at=i)
            assert c.post('/api/museum/admin/eval-runs',headers=headers,json=data).status_code==200
        response=c.get('/api/museum/admin/evaluations',headers=headers)
        board=response.json()
        assert [r['_id'] for r in board['runs']]==['3','2','1']
        assert board['total']==board['shown']==3 and not board['truncated']
        assert 'DO NOT PROJECT' not in response.text
        assert {r['id']:r['count'] for r in board['tracks']}['conservation']==0
        assert c.post('/api/museum/admin/eval-runs/1/grades',headers=headers,json=dict(
            case_id='unknown',variant='v1',reviewer='tester',facts_correct=0,facts_total=0,
            citations_supported=0,citations_total=0)).status_code==404
        display={'track':'route','scope':'Simulated route fixtures','decision':'pending','metrics':[]}
        route=run(display=display);route.update(_id='route',kind='route')
        assert c.post('/api/museum/admin/eval-runs',headers=headers,json=route).status_code==200
        assert c.get('/api/museum/admin/evaluations',headers=headers).json()['tracks'][3]['count']==1


def test_new_track_and_explicit_scope_remain_separate_from_execution_success():
    display=EvalDisplay(track='conservation',scope='Only synthetic permission cases',decision='reject',metrics=[])
    result=present_run(run(display=display.model_dump()))
    assert result['track']=='conservation' and result['decision']=='reject'
    assert result['status']=='completed' and result['scope']=='Only synthetic permission cases'


@pytest.mark.asyncio
async def test_dashboard_window_is_explicit_and_keeps_latest_in_memory_mode():
    store=RuntimeStore(MemoryStore())
    for i in range(5):
        data=run();data.update(_id=str(i),created_at=i)
        await store.insert_unique('eval_runs',data)
    board=await evaluation_dashboard(store,limit=2)
    assert board['total']==5 and board['shown']==2 and board['truncated']
    assert [r['_id'] for r in board['runs']]==['4','3']


def test_failed_crop_experiment_is_not_labelled_adopted_or_identity_accuracy():
    data=run(summary={'decision':'reject_automatic_foreground_crop','baseline':{
        'stage30':{'in_corpus':20,'out_of_corpus':10,'top1':13,'top5':14,'top10':17,'top20':19,'mrr':.7}}})
    data.update(_id='foreground-crop-v1',kind='photo')
    result=present_run(data)
    assert result['decision']=='reject' and result['status']=='completed'
    top5=next(m for m in result['metrics'] if m['label']=='Top5 命中')
    assert top5['denominator']==20 and top5['numerator']==14
    assert not result['human_reviewed'] and not result['online_ab']
