"""OCR fields must not be swallowed by an incorrect appearance description."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.museum.config import MuseumSettings
from app.museum.retrieval import MuseumIndex
from app.museum.vision import PhotoRecognizer
from app.storage.store import MemoryStore
from .test_museum_visual_retrieval import image_bytes


async def make_index():
    rows = [dict(_id=sid, title=sid, content=sid, status='active', source_hash='v1',
                 fields={'accession_number': number})
            for sid, number in [('pitcher', 'C.169-1910'), ('wrong', 'C.9-1900')]]
    store = MemoryStore()
    for row in rows:
        await store.upsert('museum_sources', row)
    index = MuseumIndex(MuseumSettings(_env_file=None, museum_embedding='lexical'), store)
    index.records = {r['_id']: r for r in rows}
    index.accessions = {'C1691910': ['pitcher'], 'C91900': ['wrong']}
    # Fault injection: ordinary semantic/keyword retrieval misses the pitcher.
    index.search = AsyncMock(return_value=[rows[1]])
    return index, store


@pytest.mark.asyncio
@pytest.mark.parametrize('conflict', [False, True])
async def test_photo_ocr_exact_number_reaches_reference_comparison(conflict):
    index, store = await make_index()
    class Visual:
        index_hash = 'fixture'
        async def search(self, raw):
            return [{'source_id': 'wrong', 'reference_id': 'wrong-ref', 'score': .9}]
        def reference_hits(self, ids):
            return [{'source_id': sid, 'reference_id': sid+'-ref'} for sid in ids]
        def reference_image(self, hit):
            return image_bytes()
    class Client:
        async def complete_json(self, messages):
            if '观察这张照片' in str(messages):
                return dict(usable=True, visible_text='C.169-1910', visual_description='蓝白盘子')
            assert '馆藏参考图，候选 id：pitcher' in str(messages)
            return {'comparisons': [{'candidate_id': 'pitcher',
                'identity': 'different_work' if conflict else 'same_work',
                'features': [], 'shared_features': ['decoration'], 'needs': ['whole']}]}
    engine = SimpleNamespace(index=index, store=store, visual_index=Visual(), client_factory=Client)
    result = await PhotoRecognizer(engine).recognize(image_bytes(), {'_id': 'session'})
    assert result['status'] == ('not_matched' if conflict else 'needs_confirmation')
    assert result['identity_confirmed'] is False
    assert result['confirmation_required'] is True
    trace = await store.get('museum_photo_traces', result['trace_id'])
    assert trace['ocr_exact_ids'] == ['pitcher']
    assert trace['photo_text_route'] == 'ocr_exact_accession'
    assert trace['reference_rescued_ids'] == ['pitcher']
    assert 'C.169-1910' not in json.dumps(trace)
    assert '蓝白盘子' not in json.dumps(trace, ensure_ascii=False)


@pytest.mark.asyncio
@pytest.mark.parametrize('text', ['C.169-1910', 'Pitcher\nC.169-1910',
    'Museum number: C.169-1910', '馆藏编号：Ｃ．１６９－１９１０', 'c 169 1910'])
async def test_exact_ocr_lines_recall_and_revalidate(text):
    index, store = await make_index()
    hits, trace = await index.search_photo_observation(text, 'blue plate')
    assert hits[0]['_id'] == 'pitcher'
    assert trace['route'] == 'ocr_exact_accession'
    assert trace['exact_ids'] == ['pitcher']


@pytest.mark.asyncio
@pytest.mark.parametrize('text,description', [('', 'C.169-1910'), ('C.169', 'plate'),
    ('C.169-191', ''), ('C.169-19100', ''), ('XC.169-1910', ''),
    ('maybe C.169-1910', ''), ('C.16g-1910', ''), ('1910', ''),
    ('https://example.org/C.169-1910', ''), ('C.169-1910-9', '')])
async def test_partial_wrong_prose_and_caption_numbers_do_not_force_exact(text, description):
    index, _ = await make_index()
    hits, trace = await index.search_photo_observation(text, description)
    assert trace['route'] == 'semantic_observation'
    assert trace['exact_ids'] == []
    assert hits[0]['_id'] == 'wrong'


@pytest.mark.asyncio
@pytest.mark.parametrize('state', ['archived', 'changed'])
async def test_stale_exact_ocr_record_is_not_resurrected(state):
    index, store = await make_index()
    source = dict(index.records['pitcher'])
    source.update({'status': 'archived'} if state == 'archived' else {'source_hash': 'new'})
    await store.upsert('museum_sources', source)
    hits, trace = await index.search_photo_observation('C.169-1910', 'plate')
    assert 'pitcher' not in [h['_id'] for h in hits]
    assert trace['exact_ids'] == []


@pytest.mark.asyncio
async def test_duplicate_number_returns_all_current_records_without_identity_claim():
    index, store = await make_index()
    twin = {**index.records['pitcher'], '_id': 'twin'}
    index.records['twin'] = twin
    index.accessions['C1691910'].append('twin')
    await store.upsert('museum_sources', twin)
    hits, trace = await index.search_photo_observation('C.169-1910', 'plate')
    assert [h['_id'] for h in hits[:2]] == ['pitcher', 'twin']
    assert trace['exact_ids'] == ['pitcher', 'twin']


@pytest.mark.asyncio
async def test_component_number_does_not_silently_equal_group_catalogue_number():
    index, _ = await make_index()
    index.accessions = {'C169A1910': ['pitcher']}
    index.records['pitcher']['fields']['accession_number'] = 'C.169&A-1910'
    _, trace = await index.search_photo_observation('C.169-1910', 'pottery bottom')
    assert trace['exact_ids'] == []


def test_ocr_export_retains_only_routing_evidence():
    from app.museum.runtime import export_metrics
    row = dict(_id='private-id', session_id='private-session', created_at=1,
        photo_text_route='ocr_exact_accession', ocr_exact_ids=['plate'],
        photo_retrieval_version='photo-ocr-field-routing-v1', visible_text='private-ocr',
        visual_description='private-description', photo='private-photo')
    exported = export_metrics(dict(traces=[], photo_traces=[row], feedback=[]))
    assert exported['rows'][0]['ocr_exact_ids'] == ['plate']
    assert exported['rows'][0]['photo_text_route'] == 'ocr_exact_accession'
    assert 'private-' not in json.dumps(exported)
