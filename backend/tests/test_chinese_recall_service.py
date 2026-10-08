import json
from types import SimpleNamespace

import pytest

from app.museum.chinese_recall import ChineseRecall
from app.museum.config import MuseumSettings
from app.museum.retrieval import MuseumIndex
from app.storage.store import MemoryStore
from app.retrieval.vector_store import MemoryVectorStore


async def make_index(tmp_path,allow_drafts=True):
    records=[dict(_id=i,title='Jug',content='briefDescription: '+text,source_hash=i,status='active',
                  source_url='https://example.org/'+i) for i,text in [('a','Blue tulips on a jug.'),('b','A red bowl.')]]
    corpus=tmp_path/'corpus.json';corpus.write_text(json.dumps(records),encoding='utf-8')
    manifest=dict(schema_version=1,version='test',records=[dict(source_id='a',source_hash='a',source_url=records[0]['source_url'],
        fields={'appearance_zh':dict(text='蓝色 郁金香',evidence_quotes=['Blue tulips'],derivation='fixture',review_status='draft')})])
    fields=tmp_path/'fields.json';fields.write_text(json.dumps(manifest),encoding='utf-8')
    cfg=MuseumSettings(_env_file=None,museum_corpus=corpus,museum_embedding='lexical',museum_search_fields=fields,
        museum_search_allow_drafts=allow_drafts,museum_chinese_recall=True,museum_text_rerank=True,deepseek_api_key='')
    store=MemoryStore();index=MuseumIndex(cfg,store);await index.start()
    index.embedding=SimpleNamespace(_local_embed=lambda texts:[[1.,0.] for _ in texts])
    index.rerank_vectors=MemoryVectorStore()
    return index,store,cfg


@pytest.mark.asyncio
async def test_chinese_fallback_and_bridged_rerank_keep_photo_path_unchanged(tmp_path):
    index,store,cfg=await make_index(tmp_path)
    original=await index.search('蓝色郁金香')
    class Worker:
        state='ready';busy=False;calls=0
        async def rank(self,q,candidates,sources):
            self.calls+=1
            text=candidates[0]['lanes']['lexical']['best_content']
            assert 'Blue tulips' in text and '郁金香' not in text
            return ['a'],dict(status='applied')
    svc=Worker();index.reranker=svc
    rows,trace=await index.search_for_answer('蓝色郁金香')
    assert [r['_id'] for r in rows]==['a'] and trace['bridge'][0]['reason']=='source_anchor'
    for state in ['busy','disabled_after_timeout']:
        svc.busy=state=='busy';svc.state='ready' if svc.busy else state
        rows,trace=await index.search_for_answer('蓝色郁金香')
        assert [r['_id'] for r in rows]==['a'] and trace['status']==state
    cfg.museum_text_rerank=False
    rows,_=await index.search_for_answer('蓝色郁金香')
    assert [r['_id'] for r in rows]==['a'] and svc.calls==1
    assert await index.search('蓝色郁金香')==original


@pytest.mark.asyncio
async def test_unreviewed_fields_not_used_for_recall_or_bridge(tmp_path):
    index,_,_=await make_index(tmp_path,allow_drafts=False)
    assert index.chinese_recall.annotations['a']['fields']=={}
    rows,_=await index.search_for_answer('蓝色郁金香')
    assert rows==[]


@pytest.mark.asyncio
async def test_source_archived_during_worker_wait_not_resurrected(tmp_path):
    index,store,_=await make_index(tmp_path)
    class Worker:
        state='ready';busy=False
        async def rank(self,q,c,s):
            source=await store.get('museum_sources','a');source['status']='archived'
            await store.upsert('museum_sources',source)
            return None,dict(status='timeout')
    index.reranker=Worker()
    rows,trace=await index.search_for_answer('蓝色郁金香')
    assert rows==[] and trace['status']=='timeout'


@pytest.mark.asyncio
async def test_changed_manifest_fails_closed(tmp_path):
    index,_,cfg=await make_index(tmp_path)
    cfg.museum_search_fields.write_text('{}',encoding='utf-8')
    with pytest.raises(ValueError,match='changed'):
        ChineseRecall(cfg.museum_search_fields,index.records,index.search_fields,index.search_fields_meta)


@pytest.mark.asyncio
async def test_missing_manifest_is_not_silently_enabled(tmp_path):
    path=tmp_path/'corpus.json'
    path.write_text(json.dumps([dict(_id='a',title='A',content='x',status='active',source_hash='a')]),encoding='utf-8')
    cfg=MuseumSettings(_env_file=None,museum_corpus=path,museum_chinese_recall=True)
    with pytest.raises(ValueError,match='manifest'):await MuseumIndex(cfg,MemoryStore()).start()


@pytest.mark.asyncio
async def test_glossary_reads_original_english_in_fallback_only(tmp_path):
    index,store,cfg=await make_index(tmp_path)
    index.reranker=SimpleNamespace(state='ready',busy=True)
    rows,_=await index.search_for_answer('红色盘子')
    assert rows==[]
    cfg.museum_fallback_glossary=True
    rows,trace=await index.search_for_answer('红色盘子')
    assert [r['_id'] for r in rows]==['b'] and trace['status']=='busy'
    assert trace['fallback_glossary']['matched']['红色']=='red'
    assert rows[0]['content']=='briefDescription: A red bowl.'
    assert await index.search('红色盘子')==[]  # Photo path unchanged.
    cfg.museum_fallback_glossary=False
    rows,_=await index.search_for_answer('红色盘子')
    assert rows==[]
