from app.museum.rerank_selection import protected_pool


def test_preserves_lane_leaders_and_fallback_before_filling():
    candidates=[dict(source_id=str(i),lanes={'lexical':dict(work_rank=i+1),'dense':dict(work_rank=20-i)}) for i in range(20)]
    sources=[dict(_id=str(i),title=f'Unique vase {i}') for i in range(20)]
    selected,audit=protected_pool('a vase',candidates,sources,['14','15','16','17','18'],8)
    ids=[r['source_id'] for r in selected]
    assert set(['0','19','14','15','16','17','18'])<=set(ids)
    assert len(ids)==8 and ids==[str(i) for i in range(20) if str(i) in ids]
    assert not audit['overflow']


def test_never_discards_protected_titles_to_meet_budget():
    candidates=[dict(source_id=str(i),lanes={}) for i in range(12)]
    sources=[dict(_id=str(i),title='Blue vase') for i in range(12)]
    selected,audit=protected_pool('Find Blue vase',candidates,sources,[],8)
    assert len(selected)==12 and audit['overflow'] and audit['dropped']==[]
