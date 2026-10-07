"""Metrics must not confuse chunk ranks, work ranks or ambiguous examples."""
import importlib.util
from pathlib import Path

spec=importlib.util.spec_from_file_location('evaluate_bm25f',Path(__file__).resolve().parents[2]/'scripts/evaluate_bm25f.py')
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_duplicate_chunks_do_not_masquerade_as_25_works():
    hits=[{'source_id':'a'}]*25+[{'source_id':'target'}]
    actual=module.stage_ranks(hits,['target'])
    assert actual==dict(best_chunk_rank=26,work_rank=2,coarse_contains_gold=False,coarse_unique_works=1)


def test_absent_target_has_no_rank():
    assert module.stage_ranks([{'source_id':'a'}],['missing'])['work_rank'] is None


def test_ambiguous_examples_only_count_once_and_only_in_top5():
    assert module.expectation_coverage(['a','b','c','d','e','f'],['a','a','f'])==dict(
        known_examples=['a','f'],present_top5=['a'],count=1,total=2)
    assert module.stats([{'gold':[],'arms':{}}])=={}
