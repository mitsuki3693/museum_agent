import copy
import importlib.util
from pathlib import Path


def evaluator(monkeypatch):
    path=Path(__file__).resolve().parents[2]/'scripts/evaluate_recall_rerank.py'
    monkeypatch.syspath_prepend(str(path.parent))
    spec=importlib.util.spec_from_file_location('recall_rerank_eval',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def test_cache_reuse_requires_same_query_order_sources_and_passages(monkeypatch):
    key=evaluator(monkeypatch).request_key
    case=dict(query='question',candidates=[dict(source_id='a',source_hash='h',passage='A'),dict(source_id='b',source_hash='i',passage='B')])
    before=key(case)
    for field,value in [('source_id','x'),('source_hash','changed'),('passage','other')]:
        changed=copy.deepcopy(case);changed['candidates'][0][field]=value
        assert key(changed)!=before
    assert key(dict(case,query='different'))!=before
    assert key(dict(case,candidates=list(reversed(case['candidates']))))!=before
    assert key(dict(case,id='different-case-label'))==before
