"""Synthetic local tiles: publication boundary and optional map delivery."""
import json

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from PIL import Image

from app.museum.api import create_app
from app.museum.config import MuseumSettings
from app.museum.floor_demo import FloorCase, FloorDemo


@pytest.fixture
def case(tmp_path):
    Image.new('RGB', (32, 32), 'white').save(tmp_path / 'tile.png')
    data = dict(id='synthetic', title='A to B', venue='Synthetic museum', level='Test floor',
                attribution='Synthetic test fixture', source_url='https://example.org/map',
                checked_at='2026-09-30', view_box=[0, 0, 32, 32],
                tiles=[dict(id='tile-1', file='tile.png', x=0, y=0, size=32)],
                waypoints=[dict(x=2, y=2, label='A', instruction='Move to B'),
                           dict(x=30, y=30, label='B', instruction='Arrived')])
    path = tmp_path / 'case.json'
    path.write_text(json.dumps(data), encoding='utf-8')
    return path, data


def test_optional_or_incomplete_map_cannot_claim_ready(case):
    path, _ = case
    assert FloorDemo(None).describe() == {'available': False}
    (path.parent / 'tile.png').unlink()
    assert FloorDemo(path).describe() == {'available': False}


def test_description_and_png_delivery_do_not_leak_local_paths(case):
    path, _ = case
    config = MuseumSettings(_env_file=None, museum_floor_demo_manifest=path)
    with TestClient(create_app(config)) as client:
        result = client.get('/api/museum/routes/floor-demo').json()
        assert result['simulated_position'] is True
        assert result['available'] is True
        assert 'file' not in result['tiles'][0]
        assert str(path.parent) not in json.dumps(result)
        tile = client.get(result['tiles'][0]['url'])
        assert tile.status_code == 200
        assert tile.headers['content-type'] == 'image/png'
        assert tile.headers['cache-control'].startswith('private')
        assert tile.content == (path.parent / 'tile.png').read_bytes()
        assert client.get('/api/museum/routes/floor-demo/tiles/unknown').status_code == 404


def test_manifest_cannot_serve_file_outside_its_directory(case):
    path, data = case
    outside = path.parent.parent / 'outside.png'
    Image.new('RGB', (1, 1)).save(outside)
    data['tiles'][0]['file'] = '../outside.png'
    path.write_text(json.dumps(data), encoding='utf-8')
    demo = FloorDemo(path)
    assert demo.tile_path('tile-1') is None
    assert demo.describe() == {'available': False}


@pytest.mark.parametrize('failure', ['outside', 'duplicate', 'same_point', 'nan'])
def test_invalid_geometry_rejected(case, failure):
    _, data = case
    if failure == 'outside':
        data['waypoints'][0]['x'] = 100
    elif failure == 'duplicate':
        data['tiles'].append(data['tiles'][0].copy())
    elif failure == 'same_point':
        data['waypoints'][1] = data['waypoints'][0].copy()
    else:
        data['view_box'][2] = float('nan')
    with pytest.raises(ValidationError):
        FloorCase.model_validate(data)
