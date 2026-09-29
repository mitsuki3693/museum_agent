"""Demonstration closures must never mutate real visit-planner state."""
import json

import pytest
from fastapi.testclient import TestClient

from app.museum.api import create_app
from app.museum.config import MuseumSettings
from app.museum.operations_demo import OperationsDemo
from app.museum.routes import RoutePlanner


@pytest.fixture
def configured(tmp_path):
    from datetime import date
    graph = dict(venue='Synthetic museum', scope='Test', version='test', synthetic=True,
        checked_at=str(date.today()), map_url='https://example.org/map', default_start='a', notice='Test fixture',
        sources=[dict(id='test',title='Fixture',url='https://example.org/map')],
        interests=[dict(id='art',label='Art')],
        nodes=[dict(id=key,title=key,level='1',kind='entrance' if key=='a' else 'gallery',
                    description='Test',interests=['art'],dwell_minutes=0 if key=='a' else 3,source_ids=['test']) for key in ['a','b','c']],
        edges=[dict(a=a,b=b,minutes=1,instruction='Synthetic connection',source_ids=['test']) for a,b in [('a','b'),('b','c'),('a','c')]])
    graph_path=tmp_path/'map.json'
    graph_path.write_text(json.dumps(graph),encoding='utf-8')
    case=dict(title='Test case',affected_id='b',preferences=dict(minutes=15,start_id='a',interests=['art']),
              people=1,humidity=67,attention_humidity=65,area=[[0,0],[10,0],[10,10]],visitors=[[5,5]],
              source_title='Synthetic fixture',source_url='https://example.org/evidence',evidence=['Synthetic summary.'])
    case_path=tmp_path/'case.json'
    case_path.write_text(json.dumps(case),encoding='utf-8')
    return graph_path,case_path


def test_preview_excludes_closed_transit_without_mutating_live_map(configured):
    graph_path,case_path=configured
    planner=RoutePlanner(graph_path)
    original=planner.map.model_dump_json()
    demo=OperationsDemo(case_path,planner)
    result=demo.describe()
    assert result['available'] and result['simulated']
    assert 'b' in [s['id'] for s in result['before']['route']['steps']]
    assert 'b' not in [s['id'] for s in result['after']['route']['steps']]
    assert all(v['id']!='b' for s in result['after']['route']['steps'] for v in s['via'])
    assert planner.map.model_dump_json()==original
    assert demo.describe()==result


def test_no_invented_detour_when_only_connection_closed(configured):
    graph_path,case_path=configured
    graph=json.loads(graph_path.read_text())
    graph['edges']=[e for e in graph['edges'] if (e['a'],e['b'])!=('a','c')]
    graph_path.write_text(json.dumps(graph),encoding='utf-8')
    result=OperationsDemo(case_path,RoutePlanner(graph_path)).describe()
    assert result['after']['status']=='route_unavailable'
    assert 'route' not in result['after']


def test_public_demo_does_not_unlock_staff_records_or_live_writes(configured):
    graph_path,case_path=configured
    config=MuseumSettings(_env_file=None,museum_route_manifest=graph_path,
                          museum_operations_demo_manifest=case_path,museum_admin_token='synthetic-test-credential')
    with TestClient(create_app(config)) as client:
        demo=client.get('/api/museum/demo/operations')
        assert demo.status_code==200 and demo.json()['simulated']
        assert client.get('/api/museum/admin/traces',headers={'X-Role':'staff'}).status_code==403
        assert client.post('/api/museum/demo/operations',json={'role':'staff','closed':'b'}).status_code==405
        assert 'b' in [n['id'] for n in client.get('/api/museum/routes/options').json()['starts']]


def test_missing_demo_configuration_is_explicitly_unavailable(configured):
    graph_path,_=configured
    assert OperationsDemo(None,RoutePlanner(graph_path)).describe()=={'available':False,'simulated':True}
