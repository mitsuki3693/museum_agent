import json
import pytest
from app.museum.config import MuseumSettings
from app.museum.retrieval import MuseumIndex
from app.museum.engine import MuseumEngine
from app.storage.store import MemoryStore


async def fixture(tmp_path,enabled=True):
    record=dict(_id='a',title='Samson Slaying a Philistine',content='A marble sculpture.',
                status='active',source_hash='h',source_url='https://example.org/a',license='fixture',
                fetched_at='2026-10-07',fields={'accession_number':'A.1-2000'})
    corpus=tmp_path/'corpus.json';corpus.write_text(json.dumps([record]))
    manifest=tmp_path/'fields.json';manifest.write_text(json.dumps(dict(schema_version=1,version='test',records=[dict(
        source_id='a',source_hash='h',source_url=record['source_url'],fields={'title_zh':dict(
            text='参孙击杀非利士人',evidence_quotes=[record['title']],evidence_scope='title',
            derivation='test translation',review_status='draft')})])),encoding='utf-8')
    config=MuseumSettings(_env_file=None,museum_corpus=corpus,museum_search_fields=manifest if enabled else None,
                          museum_search_allow_drafts=True,deepseek_api_key='')
    store=MemoryStore();index=MuseumIndex(config,store);await index.start()
    return index,store,config


@pytest.mark.asyncio
async def test_named_query_survives_unavailable_rerank_and_persists_trace(tmp_path):
    index,store,config=await fixture(tmp_path)
    assert await index.search('找参孙击杀非利士人的雕塑')==[]  # unchanged photo/legacy path
    rows,trace=await index.search_for_answer('找参孙击杀非利士人的雕塑')
    assert [r['_id'] for r in rows]==['a']
    assert trace['status']=='named_title' and trace['search_fields']['allow_drafts']
    result=await MuseumEngine(config,store,index).answer('找参孙击杀非利士人的雕塑',{'_id':'session'},'brief',None)
    assert result['retrieved_ids']==['a']
    assert (await store.get('museum_traces',result['trace_id']))['rerank']['status']=='named_title'
    assert (await store.get('museum_traces',result['trace_id']))['retrieval_route']=='named_title'


@pytest.mark.asyncio
async def test_opt_out_negation_and_explicit_scope_do_not_use_shortcut(tmp_path):
    index,_,_=await fixture(tmp_path,False)
    assert (await index.search_for_answer('参孙击杀非利士人'))[0]==[]
    index,_,_=await fixture(tmp_path)
    for query,obj,variant in [('不是参孙击杀非利士人',None,'hybrid'),('参孙击杀非利士人','missing','hybrid'),
                              ('参孙击杀非利士人',None,'lexical'),('A.1-2000',None,'hybrid')]:
        _,trace=await index.search_for_answer(query,obj,variant)
        assert trace['status']!='named_title'


@pytest.mark.asyncio
async def test_named_source_must_still_be_current(tmp_path):
    index,store,_=await fixture(tmp_path)
    row=await store.get('museum_sources','a');row['status']='archived';await store.upsert('museum_sources',row)
    assert (await index.search_for_answer('参孙击杀非利士人'))[0]==[]
