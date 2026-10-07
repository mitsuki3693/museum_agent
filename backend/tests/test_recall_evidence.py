import pytest
from app.museum.recall_evidence import bridge_candidate
from app.museum.semantic_chunks import dense_views
from app.museum.rerank_worker import score_request


def fixture():
    source=dict(_id='a',title='Jug',content='materialsAndTechniques: Clay\nbriefDescription: Blue tulips and a hole in the handle.',source_hash='h',status='active')
    annotation=dict(source_id='a',source_hash='h',fields={'appearance_zh':dict(text='蓝色 郁金香 壶柄 孔洞',evidence_quotes=['Blue tulips','hole in the handle'])})
    candidate=dict(source_id='a',lanes={'lexical':dict(best_chunk_id='a',best_content='',matched_fields=['appearance_zh'])})
    return source,annotation,candidate


def test_work_level_lexical_hit_resolves_to_original_evidence():
    s,a,c=fixture(); result,audit=bridge_candidate('郁金香和孔洞',c,s,a)
    chunk=result['lanes']['lexical']
    assert chunk['best_chunk_id'].startswith('a::')
    assert 'hole in the handle' in chunk['best_content']
    assert '郁金香' not in chunk['best_content']
    assert audit['field']=='appearance_zh'
    assert c['lanes']['lexical']['best_chunk_id']=='a'


def test_existing_chunk_evidence_stays_identical():
    s,a,c=fixture(); chunk=dense_views([s])['original'][-1]
    c['lanes']['lexical'].update(best_chunk_id=chunk['_id'],best_content=chunk['content'])
    result,_=bridge_candidate('query',c,s,a)
    assert result==c


def test_title_only_match_does_not_invent_body_evidence():
    s,a,c=fixture();a['fields']={'title_zh':dict(text='水壶',evidence_quotes=['Jug'],evidence_scope='title')}
    result,audit=bridge_candidate('水壶',c,s,a)
    assert 'lexical' not in result['lanes']
    assert audit['reason']=='title_or_no_body_anchor'


def test_stale_source_is_rejected():
    s,a,c=fixture();a['source_hash']='old'
    with pytest.raises(ValueError):bridge_candidate('郁金香',c,s,a)


def test_bridged_work_hit_runs_through_actual_worker_evidence_contract():
    s,a,c=fixture();bridged,_=bridge_candidate('郁金香和孔洞',c,s,a)
    class Model:
        def tokenizer(self,q,p,**kwargs):return {'input_ids':(q+' '+p).split()}
        def score(self,q,passages):
            assert 'hole in the handle' in passages[0]
            assert '郁金香' not in passages[0]
            return dict(scores=[1.],token_lengths=[30],truncated=0)
    assert score_request(Model(),dict(query='郁金香和孔洞',candidates=[bridged],sources=[s]))['ids']==['a']


def test_anchor_spanning_wrapped_chunks_retains_span():
    s,a,c=fixture();quote='Blue tulips and a hole in the handle'
    s['content']='briefDescription: '+'x '*94+quote+'.'
    a['fields']['appearance_zh']['evidence_quotes']=[quote]
    _,audit=bridge_candidate('郁金香',c,s,a)
    assert len(audit['chunk_ids'])==2
