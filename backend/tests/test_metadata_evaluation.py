import importlib.util
import json
from pathlib import Path

import pytest


@pytest.fixture
def evaluator():
    path=Path(__file__).resolve().parents[2]/'scripts/evaluate_metadata_queries.py'
    spec=importlib.util.spec_from_file_location('metadata_evaluation',path)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_evidence_does_not_confuse_narrative_dates_or_partial_years(evaluator):
    row={'content':'Date: 2009\nsummaryDescription: restored in 1930\nMaker: Unknown'}
    assert evaluator.metadata_lines(row,'date')==['Date: 2009']
    assert not evaluator.matches('Date: 2009','date',['200'])
    assert not evaluator.matches('Date: ca. 2009','date',['2009'])
    assert evaluator.matches('Materials and techniques: Blue tin-glazed earthenware','material',['tin-glazed','blue'])
    assert not evaluator.matches('Materials and techniques: Red tin-glazed earthenware','material',['tin-glazed','blue'])


def test_unknown_relevance_is_excluded_not_counted_as_failure(evaluator):
    rows=[{'gold':[], 'results':{'filtered':{'rank':None,'ms':1}}},
          {'gold':['a','b'], 'results':{'filtered':{'rank':2,'ms':3}}}]
    result=evaluator.summary(rows,'filtered')
    assert (result['n'],result['no_gold'],result['hit1'],result['hit5'])==(1,1,0,1)


@pytest.mark.parametrize('regressions', [[], ['M06-zh']])
def test_activation_gate_blocks_any_lost_positive(evaluator,tmp_path,monkeypatch,regressions):
    queries=tmp_path/'queries.json';queries.write_text('{}')
    report=tmp_path/'results.json'
    report.write_text(json.dumps(dict(complete=True,query_sha256=evaluator.sha(queries),
        corpora={'sample':{'changes':{'regressed':regressions}}})))
    monkeypatch.setattr(evaluator,'QUERIES',queries)
    monkeypatch.setattr(evaluator,'OUT',report)
    if regressions:
        with pytest.raises(SystemExit) as exc:evaluator.check_gate()
        assert exc.value.code==1
    else:
        evaluator.check_gate()
