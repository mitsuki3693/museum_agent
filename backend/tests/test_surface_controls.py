"""Evaluation must not hide service failures, mixed wrong identities or missing rows."""
import importlib.util
from pathlib import Path

import pytest


@pytest.fixture
def evaluator(monkeypatch):
    path=Path(__file__).resolve().parents[2]/'scripts/evaluate_surface_controls.py'
    monkeypatch.syspath_prepend(str(path.parent))
    spec=importlib.util.spec_from_file_location('surface_controls',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def row(ids=(),complete=True):
    return dict(completed=complete,candidate_ids=list(ids),number_ids=[],similar_ids=['target'])


def test_errors_are_not_safe_ood_rejection_or_success(evaluator):
    assert evaluator.outcome(row(complete=False),None)=='execution_failure'
    assert evaluator.outcome(row(complete=False),'target')=='execution_failure'
    assert evaluator.outcome(row(),None)=='no_identity'


def test_correct_plus_wrong_candidate_remains_wrong(evaluator):
    assert evaluator.outcome(row(['target','wrong']),'target')=='wrong_candidate'
    assert evaluator.outcome(row(['target']),None)=='wrong_candidate'
    assert evaluator.outcome(row(),'target')=='similar_browse'


def matrix():
    cases=[dict(case_id='01',expected='target',group='full'),dict(case_id='11',expected=None,group='ood')]
    rows=[dict(**row(['target'] if c['expected'] else []),case_id=c['case_id'],mode=m,repeat=r)
          for c in cases for m in ['legacy','surface'] for r in range(1,4)]
    return cases,rows


def test_gate_catches_missing_duplicate_capture_and_full_regression(evaluator):
    cases,rows=matrix()
    assert evaluator.gate(rows,cases,True)['passed']
    assert not evaluator.gate(rows[:-1],cases,True)['passed']
    assert not evaluator.gate(rows+[rows[-1]],cases,True)['passed']
    assert not evaluator.gate(rows,cases,False)['passed']
    next(r for r in rows if r['mode']=='surface' and r['case_id']=='01')['candidate_ids']=[]
    assert '01:full_not_preserved' in evaluator.gate(rows,cases,True)['failures']


def test_request_cap_and_reservations_survive_interruption(evaluator,tmp_path):
    for n in range(1,43):assert evaluator.reserve(tmp_path,{'kind':'synthetic'})==n
    with pytest.raises(ValueError):evaluator.reserve(tmp_path,{'kind':'synthetic'})
    assert len(list(tmp_path.glob('request-*.json')))==42
