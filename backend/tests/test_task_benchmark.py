"""Check benchmark denominators and failures, not model answer quality."""
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('task_benchmark', Path(__file__).resolve().parents[2]/'scripts/run_task_benchmark.py')
bench = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bench)


def test_wrong_candidate_cannot_pass_even_if_answered():
    result={'status':'answered','candidates':[{'id':'wrong'}]}
    assert not bench.mechanical_grade({'expect':'candidate','target':'gold'},result,{})['behavior_pass']


def test_ambiguity_requires_distinct_candidates():
    step={'expect':'ambiguous'}
    assert not bench.mechanical_grade(step,{'status':'needs_confirmation','candidates':[{'id':'same'},{'id':'same'}]}, {})['behavior_pass']
    assert bench.mechanical_grade(step,{'status':'needs_confirmation','candidates':[{'id':'a'},{'id':'b'}]}, {})['behavior_pass']


def test_selected_state_must_follow_the_new_work():
    assert not bench.mechanical_grade({'expect':'answer','selected_after':'new'},
        {'status':'answered','conversation':{'selected_object_id':'old'}},{})['behavior_pass']


def test_service_error_is_not_safe_refusal():
    assert not bench.mechanical_grade({'expect':'insufficient'},{'status':'service_unavailable'},{})['behavior_pass']


def test_exact_quote_and_behavior_are_separate():
    result={'status':'answered','claims':[{'source_id':'a','quote':'invented'}]}
    grade=bench.mechanical_grade({'expect':'answer'},result,{'a':{'content':'original'}})
    assert grade['behavior_pass'] and grade['quote_exact'] == [False]


def test_invalid_judge_counts_are_rejected():
    steps=[{'keypoints':['one','two'],'claim_count':1}]
    row={'step':1,'pass':True,'reason':'supported','keypoints_met':[True], 'citation_support':[True]}
    with pytest.raises(ValueError): bench.validate_judge({'steps':[row]},steps)
    row['keypoints_met']=[True,True];row['pass']='true'
    with pytest.raises(ValueError): bench.validate_judge({'steps':[row]},steps)


def test_missing_judgments_are_not_counted_as_passes():
    rows=[{'elapsed_ms':20,'mechanical_pass':True},
          {'elapsed_ms':30,'mechanical_pass':False,'judge':{'steps':[{'pass':True,'keypoints_met':[True],'citation_support':[]}]}}]
    score=bench.summary(rows)
    assert score['ai_judged_tasks']==1 and score['executed_tasks']==2
    assert score['ai_task_pass_among_judged']['rate']==0
    assert score['ai_citation_support']['rate'] is None
    assert score['human_reviewed_tasks']==0


def test_call_budget_cannot_be_expanded_past_authorized_ceiling():
    with pytest.raises(ValueError): bench.Budget(141)
    with pytest.raises(ValueError): bench.Budget(0)


def test_judge_cannot_pass_with_unsupported_claims():
    steps=[{'keypoints':['one'],'claim_count':1}]
    row={'step':1,'pass':True,'reason':'contradictory','keypoints_met':[True], 'citation_support':[False]}
    with pytest.raises(ValueError): bench.validate_judge({'steps':[row]},steps)


def test_unknown_maker_can_be_answered_without_inventing_identity():
    step={'expect':'acknowledge_unknown'}
    assert bench.mechanical_grade(step,{'status':'answered'}, {})['behavior_pass']
    assert bench.mechanical_grade(step,{'status':'insufficient_evidence'}, {})['behavior_pass']
    assert not bench.mechanical_grade(step,{'status':'verification_failed'}, {})['behavior_pass']


def test_unknown_identifier_may_clarify_without_fabrication():
    assert bench.mechanical_grade({'expect':'insufficient'}, {'status':'needs_clarification'}, {})['behavior_pass']


def test_judge_reference_includes_real_same_title_candidates_only():
    case={'evidence':{'first':{'content':'known'}},'steps':[{'keypoints':['two valid candidates']}]}
    rows=[{'result':{'candidates':[{'id':'second'},{'id':'nonexistent'}]}}]
    payload=bench.judge_input(case,rows,{'second':{'title':'Sculpture','content':'frozen fact'}})
    assert payload['evidence']['second']['content']=='frozen fact'
    assert 'nonexistent' not in payload['evidence']
    assert list(case['evidence'])==['first']


def test_archive_contains_ai_scores_but_no_raw_source_or_human_pass(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2]/'scripts'))
    from summarize_task_benchmark import build, database_record
    case={'id':'N01','category':'answer','steps':[{'query':'private question','keypoints':['private gold']}]}
    dataset={'cases':[case],'dataset_hash':'a'*64,'version':'test'}
    row={'case_id':'N01','category':'answer','mechanical_pass':True,'elapsed_ms':20,
         'steps':[{'result':{'status':'answered','answer':'private answer','claims':[]}}],
         'judge':{'steps':[{'pass':True,'reason':'private reason','keypoints_met':[True],'citation_support':[]}]}}
    report={'results':[row],'dataset_hash':'a'*64,'dataset_version':'test','corpus_hash':'b'*64,
            'status':'completed','summary':bench.summary([row]),'created_at':1,'model':'fixture','prompt_version':'v'*102}
    public,cards=build(dataset,report)
    record=database_record('test-run',report,public)
    import json
    assert 'private' not in json.dumps(record)
    assert 'private answer' in cards
    assert record['human_reviewed'] is False and record['results'][0]['human_grade'] is None
    assert public['groups']['answer']['executed_tasks']==1
