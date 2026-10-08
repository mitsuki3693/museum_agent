"""Small reproductions of Chinese descriptions lost among similar new works.

English catalogue text is synthetic. Dense hits are deliberately held fixed:
this exercises the production lexical/fusion path, not encoder quality.
"""
import hashlib
import json

import pytest

from app.museum.config import MuseumSettings
from app.museum.retrieval import MuseumIndex
from app.storage.store import MemoryStore


class FrozenEmbedding:
    def _local_embed(self, texts):
        return [[1.0] for _ in texts]


@pytest.mark.asyncio
@pytest.mark.parametrize('query,target,others', [
    ('找低着头的黑色石膏萨福半身像',
     'Black painted plaster bust of Sappho, a female gazing down.',
     ['Black painted plaster bust of ' + name for name in
      ['Homer', 'Virgil', 'Milton', 'Plato', 'Socrates']]),
    ('找红漆椅子，椅背中央有蝙蝠和云纹',
     'Chair in red lacquer. The back panel has bats and cloud pattern.',
     ['Chair in red lacquer with ' + motif for motif in
      ['flowers', 'dragons', 'lions', 'birds', 'leaves']]),
])
async def test_distinctive_description_survives_generic_dense_competitors(tmp_path, query, target, others):
    records = []
    for n, text in enumerate([*others, target]):
        records.append(dict(_id=f'work-{n}', title='Object', content=text,
            status='active', source_hash=hashlib.sha256(text.encode()).hexdigest(),
            source_url=f'https://example.org/works/{n}', fields={}))
    corpus, fields = tmp_path/'corpus.json', tmp_path/'fields.json'
    corpus.write_text(json.dumps(records), encoding='utf-8')
    fields.write_text(json.dumps(dict(schema_version=1, version='synthetic-v1', records=[])), encoding='utf-8')
    settings = MuseumSettings(_env_file=None, museum_corpus=corpus,
        museum_private_corpus=None, museum_embedding='lexical', museum_text_rerank=False,
        museum_search_fields=fields, museum_chinese_recall=True, museum_fallback_glossary=True)
    index = MuseumIndex(settings, MemoryStore())
    await index.start()
    index.embedding = FrozenEmbedding()
    for r in records:
        await index.vectors.add(r['_id'], [1.0], dict(source_id=r['_id'], content=r['content']))
    hits, _ = await index.search_for_answer(query)
    assert 'work-5' not in [r['_id'] for r in hits]  # Frozen legacy failure.
    settings.museum_catalogue_glossary = True
    hits, _ = await index.search_for_answer(query)
    assert 'work-5' in [r['_id'] for r in hits]
    # The optional neural worker must receive the same repaired candidates.
    class Worker:
        state = 'ready'
        busy = False

        async def rank(self, query, candidates, sources):
            assert 'work-5' in [c['source_id'] for c in candidates]
            return ['work-5'], {'status':'applied'}

    settings.museum_text_rerank = True
    index.rerank_vectors = index.vectors
    index.reranker = Worker()
    hits, trace = await index.search_for_answer(query)
    assert hits[0]['_id'] == 'work-5' and trace['status'] == 'applied'
    # Explicit selection and full identifiers remain separate authority paths.
    hits, _ = await index.search_for_answer(query, object_id='work-0')
    assert hits[0]['_id'] == 'work-0'
