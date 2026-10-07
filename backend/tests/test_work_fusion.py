import copy

import pytest

from app.museum.work_fusion import fuse_works


def hit(source, chunk):
    return dict(source_id=source,id=chunk,score=0.5,content='Same title and content')


def test_cross_lane_different_chunks_of_one_work_get_both_votes():
    rows=fuse_works([hit('b','b1'),hit('a','a1')],[hit('a','a2')])
    assert [r['source_id'] for r in rows]==['a','b']
    assert rows[0]['_rrf']==pytest.approx(1/62+1/61)
    assert rows[0]['lanes']['lexical']['best_chunk_id']=='a1'
    assert rows[0]['lanes']['dense']['best_chunk_id']=='a2'


def test_duplicate_chunks_neither_boost_score_nor_penalize_next_work_rank():
    before=[hit('a','a1'),hit('a','a2'),hit('a','a2'),hit('b','b1')]
    saved=copy.deepcopy(before)
    rows=fuse_works(before,[])
    assert rows[0]['_rrf']==pytest.approx(1/61)
    assert rows[1]['_rrf']==pytest.approx(1/62)
    assert rows[0]['lanes']['lexical']['match_count']==2
    assert rows[1]['lanes']['lexical']['best_chunk_rank']==4
    assert before==saved


def test_same_titles_do_not_merge_distinct_sources_and_ties_are_stable():
    rows=fuse_works([hit('a','x')],[hit('b','y')])
    assert [r['source_id'] for r in rows]==['a','b']
    assert len(fuse_works([hit('a','x')],[hit('b','y')],top_k=1))==1
    assert fuse_works([],[])==[]


def test_missing_source_cannot_become_a_work_candidate():
    with pytest.raises(ValueError):fuse_works([dict(id='chunk',content='unknown')],[])
