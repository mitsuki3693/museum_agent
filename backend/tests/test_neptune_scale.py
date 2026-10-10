import copy
from pathlib import Path

import pytest


@pytest.fixture
def evaluator(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2]/'scripts'))
    import evaluate_neptune_scale
    return evaluate_neptune_scale


TARGET = 'va-neptune-triton'
REFERENCE = 'va-neptune-archival-back-v1'


def test_reference_ablation_preserves_existing_assets(evaluator):
    before = {'references': [{'id': 'front', 'source_id': TARGET, 'sha256': 'original'}]}
    after = copy.deepcopy(before)
    after['references'].append({'id': REFERENCE, 'source_id': TARGET})
    evaluator.check_addition(before, after)
    after['references'][0]['sha256'] = 'changed'
    with pytest.raises(ValueError): evaluator.check_addition(before, after)


@pytest.mark.parametrize('extra', [
    {'id': REFERENCE, 'source_id': 'wrong-work'},
    {'id': 'unexpected', 'source_id': TARGET},
    {'id': 'front', 'source_id': TARGET},
])
def test_reference_ablation_rejects_invalid_addition(extra, evaluator):
    before = {'references': [{'id': 'front', 'source_id': TARGET}]}
    with pytest.raises(ValueError):
        evaluator.check_addition(before, {'references': before['references']+[extra]})


def test_target_recovery_cannot_hide_control_regression_or_prove_identity(evaluator):
    rows = [dict(id='legacy-08', in_corpus=True, baseline_rank=6, candidate_rank=1),
            dict(id='samson', in_corpus=True, baseline_rank=1, candidate_rank=2)]
    result = evaluator.retrieval_gate(rows)
    assert result['target_top3'] and not result['passed']
    assert result['rank_regressions'] == ['samson']
    rows[1]['candidate_rank'] = 1
    result = evaluator.retrieval_gate(rows)
    assert result['passed'] and not result['identity_tested'] and not result['deployment_approved']


@pytest.mark.parametrize('rank', [None, 4])
def test_unrecalled_target_cannot_pass(rank, evaluator):
    assert not evaluator.retrieval_gate([dict(id='legacy-08', in_corpus=True,
                                   baseline_rank=6, candidate_rank=rank)])['passed']
    assert not evaluator.retrieval_gate([])['passed']
