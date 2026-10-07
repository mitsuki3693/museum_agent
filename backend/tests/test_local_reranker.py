import copy

import pytest

from app.museum.local_reranker import evidence_passage,ranked_ids


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
