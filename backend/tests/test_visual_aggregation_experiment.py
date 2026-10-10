"""Fixed score fixtures validate the experiment, never model quality."""
import importlib.util
from pathlib import Path
import numpy as np

spec = importlib.util.spec_from_file_location('visual_aggregation_experiment',
    Path(__file__).resolve().parents[2]/'scripts/evaluate_visual_aggregation.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
query_coverage_scores = module.query_coverage_scores


def test_single_crop_spike_cannot_outvote_consistent_query_support():
    entries = [{'source_id': sid, 'reference_id': sid} for sid in ['spike', 'consistent'] for _ in range(5)]
    values = np.full((5, 10), .4, dtype=np.float32)
    values[0, :5] = .99
    values[:, 5:] = .7
    hits = query_coverage_scores(values, entries)
    assert [h['source_id'] for h in hits] == ['consistent', 'spike']


def test_multiple_references_do_not_accumulate_support_or_duplicate_works():
    entries = [{'source_id': 'a', 'reference_id': str(i)} for i in range(2) for _ in range(5)]
    values = np.full((5, 10), .1, dtype=np.float32)
    values[:3, :5] = .9
    values[3:, 5:] = .9
    hits = query_coverage_scores(values, entries)
    assert len(hits) == 1
    assert abs(hits[0]['score'] - .58) < 1e-6
