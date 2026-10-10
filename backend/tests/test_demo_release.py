"""The release guard must detect drift without exposing private configuration."""
import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('demo_release', Path(__file__).parents[2] / 'scripts/freeze_demo_release.py')
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


@pytest.fixture
def frozen(tmp_path):
    (tmp_path / 'backend/app').mkdir(parents=True)
    (tmp_path / 'backend/app/api.py').write_text('VERSION = 1\n')
    (tmp_path / '.env').write_text('API_KEY=private-secret\n')
    (tmp_path / 'corpus.json').write_text('[]')
    health = {'status': 'ok', **{key: 'v1' for key in release.HEALTH_FIELDS},
              'text_rerank': {key: 1 for key in release.RERANK_FIELDS},
              'extra_secret': 'never-export'}
    target = release.capture(tmp_path, 'demo-v1', health, [tmp_path / 'corpus.json'], 'test-model')
    return tmp_path, health, target


def test_freeze_is_immutable_and_health_is_allowlisted(frozen):
    root, health, target = frozen
    assert release.verify(root, 'demo-v1', health) == []
    raw = (target / 'manifest.json').read_text()
    assert 'private-secret' not in raw and 'never-export' not in raw
    assert (target / 'config.env').read_bytes() == (root / '.env').read_bytes()
    with pytest.raises(FileExistsError):
        release.capture(root, 'demo-v1', health, [root / 'corpus.json'], 'test-model')


@pytest.mark.parametrize('file,expected', [
    ('backend/app/api.py', 'application_source'),
    ('corpus.json', 'data:corpus.json'),
    ('.env', 'configuration:.env'),
    ('.runtime/releases/demo-v1/config.env', 'configuration:.runtime/releases/demo-v1/config.env'),
])
def test_changes_are_detected_without_restoring_or_deleting(frozen, file, expected):
    root, health, _ = frozen
    (root / file).write_text('changed')
    assert expected in release.verify(root, 'demo-v1', health)
    assert (root / file).read_text() == 'changed'


def test_missing_data_and_new_source_are_drift(frozen):
    root, health, _ = frozen
    (root / 'corpus.json').unlink()
    (root / 'backend/app/extra.py').write_text('x=1')
    assert set(release.verify(root, 'demo-v1', health)) == {'application_source', 'data:corpus.json'}


def test_runtime_change_is_detected_but_operational_counters_are_ignored(frozen):
    root, health, _ = frozen
    health['text_rerank']['restarts'] = 3
    assert release.verify(root, 'demo-v1', health) == []
    health['photo_verification'] = 'surface'
    assert release.verify(root, 'demo-v1', health) == ['live_version']


def test_manifest_cannot_reach_outside_project(frozen):
    root, health, target = frozen
    path = target / 'manifest.json'
    data = json.loads(path.read_text())
    data['data_files'] = {'../outside': 'hash'}
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='remain in the project'):
        release.verify(root, 'demo-v1', health)


@pytest.mark.parametrize('name', ['../elsewhere', '/tmp/release', 'demo/v1', ''])
def test_release_name_cannot_escape_private_directory(tmp_path, name):
    with pytest.raises(ValueError):
        release.release_dir(tmp_path, name)


def test_unhealthy_or_incomplete_runtime_cannot_be_frozen():
    with pytest.raises(ValueError):
        release.version({'status': 'error'})
    with pytest.raises(KeyError):
        release.version({'status': 'ok'})
