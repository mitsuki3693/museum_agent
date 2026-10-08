import pytest

from app.museum.evidence_ties import stable_evidence_ranking
from app.museum.local_reranker import ranked_ids


def test_duplicate_evidence_cannot_swap_identity_from_batch_rounding():
    candidates=[{'source_id':i} for i in ['a','b','c']]
    old=[1.940991759,1.940991759,1.5]
    new=[1.940991759,1.940993309,1.5]
    assert ranked_ids(candidates,old)!=ranked_ids(candidates,new)  # Original bug.
    for scores in [old,new]:
        ids,audit=stable_evidence_ranking(candidates,scores,['same','same','other'])
        assert ids==['a','b','c']
        assert audit[0]['source_ids']==['a','b'] and 'same' not in audit[0]
    assert new==[1.940991759,1.940993309,1.5]


def test_distinct_evidence_keeps_its_slots_even_between_group_members():
    candidates=[{'source_id':i} for i in ['a','b','c','d']]
    scores=[1.,3.,2.,1.000001]
    ids,_=stable_evidence_ranking(candidates,scores,['same','same','different','same '])
    assert ids==['a','c','d','b']  # c/d stay in raw score positions 1/2.
    assert scores==[1.,3.,2.,1.000001]


def test_different_passages_never_merge_despite_nearly_equal_scores():
    candidates=[{'source_id':'a'},{'source_id':'b'}]
    ids,audit=stable_evidence_ranking(candidates,[1.,1.00000001],['Vase','vase'])
    assert ids==['b','a'] and audit==[]
    assert stable_evidence_ranking([],[],[])==([],[])


@pytest.mark.parametrize('scores,passages', [([1.],['p','p']),([1.,float('nan')],['p','p']),([1.,2.],['p']),([1.,2.],['p',''])])
def test_invalid_scores_or_evidence_fail_closed(scores,passages):
    with pytest.raises(ValueError):
        stable_evidence_ranking([{'source_id':'a'},{'source_id':'b'}],scores,passages)
