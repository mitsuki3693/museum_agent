import copy

import pytest

from app.museum.local_reranker import evidence_passage,ranked_ids,contextual_evidence
from app.museum.semantic_chunks import dense_views


def test_passage_keeps_attributes_and_retrieved_evidence_without_gold_or_source_edit():
    source=dict(_id='a',title='Work',content='Maker: Alice\nDate: 1700\nMaterials and techniques: Marble\nMuseum number: 12\nOther: unselected fact')
    before=copy.deepcopy(source)
    candidate=dict(source_id='a',lanes={'dense':dict(best_content='Work\nA carved face'),
                                      'lexical':dict(best_content='Work\nA carved face')})
    passage=evidence_passage(source,candidate)
    assert 'Maker: Alice' in passage and 'Materials and techniques: Marble' in passage
    assert passage.count('A carved face')==1
    assert 'Other:' not in passage and 'Museum number:' not in passage
    assert source==before
    with pytest.raises(ValueError):evidence_passage(source,dict(source_id='b'))


def test_ranking_changes_only_order_and_preserves_ties():
    candidates=[{'source_id':c} for c in 'abc']
    assert ranked_ids(candidates,[0,1,1])==['b','c','a']
    assert ranked_ids([],[])==[]


@pytest.mark.parametrize('scores', [[1],[1,float('nan')],[1,float('inf')]])
def test_invalid_scores_cannot_silently_drop_candidates(scores):
    with pytest.raises(ValueError):ranked_ids([dict(source_id='a'),dict(source_id='b')],scores)


def context_fixture():
    source=dict(_id='a',title='Work',content='Title: Work\nMuseum number: 99\nMaker: Alice\nDate:\n'
        'briefDescription: A sculpture\nsummaryDescription: A maker supplied the models.\n'
        'physicalDescription: A woman and lion with six detachable figures.\n'
        'A raised hand.\nA blue lion head.\nA wooden body.\nA base.')
    chunks=dense_views([source])['original']
    winner=next(c for c in chunks if c['content'].endswith('A blue lion head.'))
    candidate=dict(source_id='a',lanes={'dense':dict(best_chunk_id=winner['_id'],best_content=winner['content'])})
    return source,candidate,chunks


def test_context_enriches_all_candidates_with_traceable_source_only():
    source,candidate,chunks=context_fixture();before=copy.deepcopy((source,candidate,chunks))
    result=contextual_evidence(source,candidate,chunks,fits=lambda _:True)
    assert 'six detachable figures' in result['passage']
    assert 'supplied the models' in result['passage']
    assert 'Museum number' not in result['passage'] and 'Date:' not in result['passage']
    assert result['passage'].count('A blue lion head.')==1
    for item in result['included']:
        if item['chunk_id']:
            assert next(c['content'] for c in chunks if c['_id']==item['chunk_id'])=='Work\n'+item['text']
    assert before==(source,candidate,chunks)
    assert result==contextual_evidence(source,candidate,chunks,fits=lambda _:True)


def test_context_budget_records_omissions_without_slicing_excerpts():
    source,candidate,chunks=context_fixture()
    result=contextual_evidence(source,candidate,chunks,fits=lambda p:len(p)<=65)
    assert len(result['passage'])<=65 and result['omitted']
    assert 'A blue lion head.' in result['passage']
    assert all(item['text'] not in result['passage'] for item in result['omitted'])
    with pytest.raises(ValueError,match='Budget'):
        contextual_evidence(source,candidate,chunks,fits=lambda _:False)


def test_context_rejects_wrong_source_or_changed_retrieved_text():
    source,candidate,chunks=context_fixture()
    with pytest.raises(ValueError,match='Cross-source'):
        contextual_evidence(source,candidate,chunks+[dict(source_id='b')],fits=lambda _:True)
    candidate['lanes']['dense']['best_content']='tampered'
    with pytest.raises(ValueError,match='Changed'):
        contextual_evidence(source,candidate,chunks,fits=lambda _:True)


def test_context_neighbours_do_not_wrap_at_start_or_end():
    source=dict(_id='a',title='Work',content='First\nSecond\nThird\nFourth\nLast')
    chunks=dense_views([source])['original']
    for idx,forbidden in [(0,'Last'),(4,'First')]:
        candidate=dict(source_id='a',lanes={'dense':dict(best_chunk_id=chunks[idx]['_id'],best_content=chunks[idx]['content'])})
        result=contextual_evidence(source,candidate,chunks,fits=lambda _:True)
        assert forbidden not in result['passage']
