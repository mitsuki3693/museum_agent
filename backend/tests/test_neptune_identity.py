from pathlib import Path
import pytest


@pytest.fixture
def evaluator(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2]/'scripts'))
    import evaluate_neptune_identity
    return evaluate_neptune_identity


def matrix():
    cases=[dict(case_id='08',expected='target'),dict(case_id='12',expected=None)]
    rows=[dict(case_id=c['case_id'],mode=m,repeat=r,completed=True,references_complete=True,
               candidate_ids=['target'] if c['expected'] else [],number_ids=[],similar_ids=[])
        for c in cases for m in ['original','back_reference'] for r in range(1,4)]
    return cases,rows


def test_gate_rejects_incomplete_capture_and_matrix(evaluator):
    cases,rows=matrix()
    assert evaluator.gate(rows,cases,True)['passed']
    assert not evaluator.gate(rows,cases,False)['passed']
    assert not evaluator.gate(rows[:-1],cases,True)['passed']
    assert not evaluator.gate(rows+[rows[-1]],cases,True)['passed']


@pytest.mark.parametrize('change',['error','omission','wrong','mixed','no_identity'])
def test_candidate_failure_cannot_count_as_success(evaluator,change):
    cases,rows=matrix()
    item=next(r for r in rows if r['case_id']=='08' and r['mode']=='back_reference')
    if change=='error':item['completed']=False
    elif change=='omission':item['references_complete']=False
    elif change=='wrong':item['candidate_ids']=['wrong']
    elif change=='mixed':item['candidate_ids']=['target','wrong']
    else:item['candidate_ids']=[]
    assert not evaluator.gate(rows,cases,True)['passed']


def test_budget_survives_partial_run(evaluator,tmp_path):
    for n in range(1,29):assert evaluator.reserve(tmp_path,{'kind':'synthetic'})==n
    with pytest.raises(ValueError):evaluator.reserve(tmp_path,{'kind':'synthetic'})
    assert len(list(tmp_path.glob('request-*.json')))==28
