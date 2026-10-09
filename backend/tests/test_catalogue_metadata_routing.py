"""Synthetic catalogue cases: prose mentions are not author/date fields."""
import hashlib
import json

import pytest

from app.museum.config import MuseumSettings
from app.museum.retrieval import MuseumIndex
from app.storage.store import MemoryStore


async def make_index(tmp_path):
    specs = [(f'other-{i}', 'Unrelated artist', '1800',
              'Find works by Giovanni Bologna. Find works dated 1930.') for i in range(6)]
    specs += [('bologna', 'Bologna, Giovanni', '1560-1562', 'Marble sculpture.'),
              ('wood', 'Grant Wood (American, 1891–1942)', '1930', 'An oil painting.')]
    records = []
    for sid, maker, date, body in specs:
        content = f'Maker: {maker}\nDate: {date}\nDescription: {body}'
        records.append(dict(_id=sid, title='Work', status='active', content=content,
            source_hash=hashlib.sha256(content.encode()).hexdigest(), source_url='https://example.org/'+sid,
            fields=dict(artist_display=maker),license='synthetic',fetched_at='2026-10-09'))
    path = tmp_path/'corpus.json'
    path.write_text(json.dumps(records),encoding='utf-8')
    cfg = MuseumSettings(_env_file=None, museum_corpus=path, museum_embedding='lexical',
                         museum_metadata_routing=True, deepseek_api_key='')
    store = MemoryStore()
    index = MuseumIndex(cfg,store)
    await index.start()
    return index,store


@pytest.mark.asyncio
@pytest.mark.parametrize('query,gold', [('Find works by Giovanni Bologna','bologna'),
                                      ('Find works dated 1930','wood')])
async def test_explicit_metadata_ignores_incidental_prose(tmp_path,query,gold):
    index,_ = await make_index(tmp_path)
    rows,_ = await index.search_for_answer(query)
    assert [r['_id'] for r in rows] == [gold]


@pytest.mark.parametrize('left,right,same', [
    ('1930','ca. 1930',False), ('1930','1930-1959',False),
    ('circa 1695','约1695年',True), ('1750–1775','1750-1775',True),
])
def test_date_equality_preserves_uncertainty_and_ranges(left,right,same):
    from app.museum.catalogue_metadata import date_key
    assert (date_key(left)==date_key(right)) is same


@pytest.mark.parametrize('query', [
    '1930', 'Find works not dated 1930', 'Find works dated 1930 or 1940',
    'Find works dated 1930 with red flowers', '找1930年代的作品',
    '找蓝色的作品', '这件作品的作者是谁？', '不要莫奈的作品',
    'Find ceramics by the Greek A factory', 'Find works by the De Roos factory',
    'Find works by Giovanni Bologna dated 1560', '找莫奈和梵高的作品',
])
def test_unsupported_or_compound_queries_keep_original_path(query):
    from app.museum.catalogue_metadata import CatalogueMetadata
    assert CatalogueMetadata({}).parse(query) is None


@pytest.mark.asyncio
async def test_source_validation_selection_and_rollback(tmp_path):
    index,store = await make_index(tmp_path)
    q='Find works by Giovanni Bologna'
    original=await index.search(q)
    assert original[0]['_id']=='other-0'  # Photo/direct search unchanged.
    rows,_=await index.search_for_answer(q,object_id='other-0')
    assert rows[0]['_id']=='other-0'
    row=await store.get('museum_sources','bologna');row['status']='archived'
    await store.upsert('museum_sources',row)
    assert (await index.search_for_answer(q))[0]==[]
    row['status']='active';row['source_hash']='changed';await store.upsert('museum_sources',row)
    assert (await index.search_for_answer(q))[0]==[]
    index.settings.museum_metadata_routing=False
    assert (await index.search_for_answer(q))[0]==original


@pytest.mark.asyncio
async def test_metadata_fresh_selection_has_no_model_call_and_is_traced(tmp_path):
    from app.museum.engine import MuseumEngine
    index,store=await make_index(tmp_path)
    index.settings.deepseek_api_key='synthetic'
    class NoModel:
        calls=0
        async def complete_json(self,*args,**kwargs):
            self.calls+=1
            raise AssertionError('Explicit field lookup must not generate or rewrite')
    client=NoModel()
    engine=MuseumEngine(index.settings,store,index,lambda:client)
    session={'_id':'session','object_id':'other-0','history':[{'role':'user','content':'之前的作品'}]}
    result=await engine.answer('找年代标注为1930的作品',session,'brief',None)
    assert result['status']=='needs_confirmation'
    assert [r['id'] for r in result['candidates']]==['wood']
    assert result['usage']==[] and session.get('object_id') is None
    trace=await store.get('museum_traces',result['trace_id'])
    assert trace['retrieval_route']=='catalogue_metadata'
    assert trace['rerank']['metadata']['evidence']=={'wood':'Date: 1930'}
    absent=await engine.answer('Find works dated 2029',session,'brief',None)
    assert absent['status']=='insufficient_evidence' and absent['retrieved_ids']==[]
    assert absent['usage']==[]
    assert client.calls==0


def test_author_equality_and_qualified_authorship():
    from app.museum.catalogue_metadata import author_key,CatalogueMetadata
    assert author_key('Bologna, Giovanni')==author_key('Giovanni Bologna')
    assert author_key('Giovanni Francesco')!=author_key('Giovanni Bologna')
    assert author_key('Grant Wood (American, 1891–1942)')==author_key('Grant Wood')
    assert author_key('Grant Wood (attributed to)') is None
    assert author_key('Workshop of Giovanni Bologna') is None
    meta=CatalogueMetadata({'a':dict(content='Maker: Bologna, Giovanni\nDate: 1930'),
                            'b':dict(content='Maker: Bologna, Giovanni\nDate: 1930')})
    assert set(meta.lookup(meta.parse('找乔瓦尼·博洛尼亚的作品')))=={'a','b'}
    assert set(meta.lookup(meta.parse('Find works dated 1930')))=={'a','b'}
