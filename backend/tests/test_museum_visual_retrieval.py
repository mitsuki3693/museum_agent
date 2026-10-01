"""Candidate omission regression: a mistaken caption must not gate visual recall."""
import io
import hashlib
import json
from types import SimpleNamespace
import pytest
from app.museum.vision import PhotoRecognizer
from app.storage.store import MemoryStore
from PIL import Image
import numpy as np
from app.museum.visual_index import MuseumVisualIndex


def image_bytes():
    out = io.BytesIO()
    Image.new("RGB", (32, 24), "blue").save(out, format="JPEG")
    return out.getvalue()


@pytest.mark.asyncio
async def test_visual_candidate_survives_incorrect_caption():
    correct = {"_id": "sculpture", "title": "Sculpture", "content": "A figure blowing a shell",
               "source_hash": "v1", "status": "active", "source_url": "https://example.org/work"}
    wrong = {"_id": "painting", "title": "Painting", "content": "Fabric", "source_url": "https://example.org/painting"}
    store = MemoryStore()
    await store.upsert("museum_sources", correct)
    class TextIndex:
        records = {"sculpture": correct, "painting": wrong}
        async def search(self, query):
            return [wrong]
    class VisualIndex:
        index_hash = "frozen-fixture"
        async def search(self, raw):
            return [{"source_id": "sculpture", "score": .7, "reference_id": "front"}]
        def reference_image(self, hit):
            return image_bytes()
    class Client:
        async def complete_json(self, messages):
            if "观察这张照片" in str(messages):
                return {"usable": True, "visible_text": "", "visual_description": "Folded fabric"}
            # Not a canned ID: only return the work if it actually reached comparison.
            supplied = str(messages)
            return {"comparisons": [{"candidate_id":"sculpture", "identity":"same_work", "features":[
                {"part":part,"query_detail":"visible shell position","reference_detail":"same shell position","relation":"match","distinctive":True}
                for part in ["pose","parts"]],"shared_features":["figures"],"needs":[]}] if '"id": "sculpture"' in supplied else []}
    engine = SimpleNamespace(store=store, index=TextIndex(), visual_index=VisualIndex(), client_factory=Client)
    result = await PhotoRecognizer(engine).recognize(image_bytes(), {"_id": "s"})
    assert [c["id"] for c in result["candidates"]] == ["sculpture"]
    assert result["status"] == "needs_confirmation"
    assert "answer" not in result
    trace = await store.get("museum_photo_traces", result["trace_id"])
    assert trace["visual_retrieved_ids"] == ["sculpture"]
    assert "data:image" not in json.dumps(trace)


class ColorEncoder:
    """Deterministic fixture encoder: tests ranking/grouping, not model quality."""
    def encode(self, images):
        values = np.array([np.asarray(i).mean(axis=(0, 1)) for i in images])
        return values / np.linalg.norm(values, axis=1, keepdims=True)


def gallery(tmp_path):
    rows, records = [], {}
    for key, source, color in [('red-front', 'red', 'red'), ('red-back', 'red', 'red'), ('blue', 'blue', 'blue')]:
        path = tmp_path / (key + '.jpg')
        Image.new('RGB', (40, 60), color).save(path)
        records[source] = {'_id': source, 'status': 'active'}
        rows.append(dict(id=key, source_id=source, path=path.name,
                         sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                         source_url='https://example.org/' + key, license='test'))
    manifest = tmp_path / 'references.json'
    manifest.write_text(json.dumps({'version': 1, 'references': rows}), encoding='utf-8')
    return manifest, records


@pytest.mark.asyncio
async def test_visual_groups_views_and_returns_one_hit_per_work(tmp_path):
    manifest, records = gallery(tmp_path)
    index = MuseumVisualIndex(manifest, tmp_path, records, encoder=ColorEncoder())
    await index.start()
    hits = await index.search((tmp_path / 'red-front.jpg').read_bytes())
    assert [r['source_id'] for r in hits] == ['red', 'blue']
    assert hits[0]['score'] > hits[1]['score']
    assert index.reference_image(hits[0]).startswith(b'\xff\xd8')


@pytest.mark.asyncio
async def test_identity_policy_applies_to_work_across_reference_views(tmp_path):
    manifest, records = gallery(tmp_path)
    data = json.loads(manifest.read_text(encoding='utf-8'))
    data['references'][0]['identity_requires_label'] = True
    manifest.write_text(json.dumps(data), encoding='utf-8')
    index = MuseumVisualIndex(manifest, tmp_path, records, encoder=ColorEncoder())
    await index.start()
    assert index.label_required_ids == {'red'}
    data['references'][0]['identity_requires_label'] = 'false'
    manifest.write_text(json.dumps(data), encoding='utf-8')
    with pytest.raises(ValueError, match='must be a boolean'):
        await MuseumVisualIndex(manifest, tmp_path, records, encoder=ColorEncoder()).start()


@pytest.mark.asyncio
@pytest.mark.parametrize('damage', ['hash', 'outside', 'unknown', 'duplicate', 'provenance'])
async def test_reference_manifest_fails_closed(tmp_path, damage):
    manifest, records = gallery(tmp_path)
    spec = json.loads(manifest.read_text())
    row = spec['references'][0]
    if damage == 'hash':
        row['sha256'] = 'tampered'
    elif damage == 'outside':
        row['path'] = '../outside.jpg'
    elif damage == 'unknown':
        row['source_id'] = 'not-in-corpus'
    elif damage == 'duplicate':
        spec['references'].append(row.copy())
    else:
        row.pop('license')
    manifest.write_text(json.dumps(spec), encoding='utf-8')
    with pytest.raises(ValueError):
        await MuseumVisualIndex(manifest, tmp_path, records, encoder=ColorEncoder()).start()


@pytest.mark.asyncio
@pytest.mark.parametrize('state', ['active', 'archived', 'changed', 'unavailable'])
async def test_visual_is_candidate_only_and_rechecks_source(state):
    expected = {'_id': 's', 'title': 'Sculpture', 'content': 'Marble', 'status': 'active', 'source_hash': 'original'}
    record = dict(expected)
    if state == 'archived':
        record['status'] = 'archived'
    if state == 'changed':
        record['source_hash'] = 'new-content'
    store = MemoryStore()
    await store.upsert('museum_sources', record)
    class Index:
        records = {'s': expected}
        async def search(self, query):
            return []
    class Visual:
        index_hash = 'fixture'
        async def search(self, raw):
            if state == 'unavailable':
                raise RuntimeError('Do not log private paths')
            return [{'source_id': 's', 'reference_id': 'front', 'score': .99}]
        def reference_image(self, hit):
            return image_bytes()
    class Client:
        async def complete_json(self, messages):
            if '观察这张照片' in str(messages):
                return {'usable': True, 'visible_text': '', 'visual_description': ''}
            # Nearest neighbour, including score .99, must not override rejection.
            return {'comparisons': []}
    engine = SimpleNamespace(index=Index(), visual_index=Visual(), store=store, client_factory=Client)
    result = await PhotoRecognizer(engine).recognize(image_bytes(), {'_id': 'session'})
    trace = await store.get('museum_photo_traces', result['trace_id'])
    assert result['status'] == 'not_matched'
    assert trace['compared_ids'] == (['s'] if state == 'active' else [])
    if state == 'unavailable':
        assert trace['visual_error'] == 'RuntimeError'
        assert 'private paths' not in json.dumps(trace)
