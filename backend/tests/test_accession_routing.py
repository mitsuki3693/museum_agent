import hashlib
import json
import pytest
from app.museum.config import MuseumSettings
from app.museum.retrieval import MuseumIndex
from app.museum.engine import MuseumEngine
from app.storage.store import MemoryStore


async def build(tmp_path, numbers=('C.2359-1910','C.2367-1910')):
    rows=[]
    for i,number in enumerate(numbers):
        content=f'Museum number: {number}. Bronze object.'
        rows.append(dict(_id=f'w{i}',title='Test object',content=content,
            fields={'accession_number':number},status='active',source_hash=hashlib.sha256(content.encode()).hexdigest(),
            source_url=f'https://example.org/{i}',license='fixture',fetched_at='2026-10-07'))
    path=tmp_path/'corpus.json';path.write_text(json.dumps(rows),encoding='utf-8')
    cfg=MuseumSettings(_env_file=None,museum_corpus=path,museum_embedding='lexical',deepseek_api_key='fixture')
    store=MemoryStore();index=MuseumIndex(cfg,store);await index.start()
    return cfg,store,index


@pytest.mark.asyncio
@pytest.mark.parametrize('query',['C.2359-1910','c 2359 1910','C-2359-1910','Ｃ．２３５９－１９１０','C23591910'])
async def test_full_number_bypasses_fuzzy_candidates(tmp_path,query):
    _,_,index=await build(tmp_path)
    index.bm25.search=lambda *a,**k: (_ for _ in ()).throw(AssertionError('Exact route entered fuzzy ranking'))
    assert [r['_id'] for r in await index.search(query)]==['w0']


@pytest.mark.asyncio
async def test_collision_keeps_all_and_partial_or_prose_is_not_exact(tmp_path):
    _,_,index=await build(tmp_path,('C.12-345','C.123-45'))
    assert [r['_id'] for r in await index.search('c12345')]==['w0','w1']
    for query in ['C12','C12346','C12345?','介绍 C12345','C12345 and C999']:
        assert index.exact_accession_ids(query)==[]


@pytest.mark.asyncio
async def test_exact_match_still_checks_active_source_hash_and_context(tmp_path):
    _,store,index=await build(tmp_path)
    assert (await index.search('C.2359-1910',object_id='w1'))[0]['_id']=='w1'
    assert await index.search('C.2359-1910',object_id='missing')==[]
    row=await store.get('museum_sources','w0');row['status']='archived';await store.upsert('museum_sources',row)
    assert await index.search('C.2359-1910')==[]
    row['status']='active';row['source_hash']='changed';await store.upsert('museum_sources',row)
    assert await index.search('C.2359-1910')==[]


@pytest.mark.asyncio
async def test_engine_exact_lookup_needs_confirmation_without_llm_and_is_traced(tmp_path):
    cfg,store,index=await build(tmp_path)
    class NoModel:
        calls=0
        async def complete_json(self,messages):
            self.calls+=1
            raise AssertionError('Bare accession lookup must not use the model')
    client=NoModel();engine=MuseumEngine(cfg,store,index,lambda:client)
    session={'_id':'s','object_id':'w1','history':[{'role':'user','content':'介绍前一件'}]}
    result=await engine.answer('c 2359 1910',session,'brief',None)
    assert result['status']=='needs_confirmation'
    assert [c['id'] for c in result['candidates']]==['w0']
    assert client.calls==0
    trace=await store.get('museum_traces',result['trace_id'])
    assert trace['retrieval_route']=='exact_accession'
    assert session.get('object_id') is None
