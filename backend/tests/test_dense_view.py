import hashlib
import json
import sys
from types import SimpleNamespace

import pytest

from app.museum.config import MuseumSettings
from app.museum.engine import MuseumEngine
from app.museum.retrieval import MuseumIndex
from app.storage.store import MemoryStore


class FakeEmbedding:
    def __init__(self, settings):
        pass

    def _local_embed(self, texts):
        assert texts, 'Empty semantic corpus must skip document encoding'
        return [[1.0] + [0.0] * 383 for _ in texts]


@pytest.mark.asyncio
@pytest.mark.parametrize('body', [True, False])
async def test_filtered_index_preserves_sources_lexical_exact_and_rollback(tmp_path, monkeypatch, body):
    monkeypatch.setattr('app.museum.retrieval.EmbeddingClient', FakeEmbedding)
    monkeypatch.setitem(sys.modules, 'sentence_transformers', SimpleNamespace(SentenceTransformer=lambda *a, **k: object()))
    content = 'Museum number: C.1-2000\nDate: 1700'
    if body:
        content += '\nphysicalDescription: A tall blue ceramic flower vase.'
    row = dict(_id='fixture', title='Vase', content=content, status='active',
               fields={'accession_number': 'C.1-2000'},
               source_hash=hashlib.sha256(content.encode()).hexdigest(),
               source_url='https://example.org/vase', license='fixture', fetched_at='2026-10-07')
    path = tmp_path / 'corpus.json'
    path.write_text(json.dumps([row]), encoding='utf-8')
    initial_bytes = path.read_bytes()
    indices = []
    for view in ['original', 'filtered', 'original']:
        cfg = MuseumSettings(_env_file=None, museum_corpus=path, museum_private_corpus=None,
                             museum_embedding='local', museum_dense_view=view, deepseek_api_key='')
        store = MemoryStore()
        index = MuseumIndex(cfg, store)
        await index.start()
        indices.append(index)
        assert await store.get('museum_sources', 'fixture') == row
        assert [r['_id'] for r in await index.search('c 1 2000')] == ['fixture']
        assert [r['_id'] for r in await index.search('1700', variant='lexical')] == ['fixture']
        engine = MuseumEngine(cfg, store, index, lambda: SimpleNamespace(usage_records=[]))
        result = await engine.answer('C.1-2000', {'_id': 's'}, 'brief', None)
        trace = await store.get('museum_traces', result['trace_id'])
        assert trace['dense_view'] == view
        assert trace['dense_view_version'] == ('semantic-content-v1' if view == 'filtered' else 'legacy')
    assert indices[0].vectors._meta == indices[2].vectors._meta
    assert set(indices[1].vectors._meta) == ({'fixture::2'} if body else set())
    assert indices[0].bm25.search('1700', top_k=25) == indices[1].bm25.search('1700', top_k=25)
    assert path.read_bytes() == initial_bytes
