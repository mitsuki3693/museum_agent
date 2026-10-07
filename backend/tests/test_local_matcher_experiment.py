"""Experimental ranking controls; no weights or optional matcher dependencies."""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location('local_match_experiment', Path(__file__).resolve().parents[2]/'scripts/evaluate_local_matcher.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_no_local_evidence_keeps_global_order():
    hits = [{'source_id': 'a'}, {'source_id': 'b'}, {'source_id': 'c'}]
    assert module.rerank(hits, []) == hits
    assert module.rerank(hits, [{'source_id': 'c', 'inliers': 11, 'query_grid_cells': 12}]) == hits


def test_concentrated_matches_do_not_override_global_order():
    hits = [{'source_id': 'a'}, {'source_id': 'b'}]
    assert module.rerank(hits, [{'source_id': 'b', 'inliers': 100, 'query_grid_cells': 1}]) == hits


def test_supported_rerank_preserves_candidates_and_stable_ties():
    hits = [{'source_id': 'a'}, {'source_id': 'b'}, {'source_id': 'c'}]
    evidence = [{'source_id': name, 'inliers': 12, 'query_grid_cells': 4} for name in ('b', 'c')]
    assert [r['source_id'] for r in module.rerank(hits, evidence)] == ['b', 'c', 'a']
    assert module.rank(['b','c','a'], 'outside') is None
    assert module.rank(['b','c','a'], 'a') == 3
