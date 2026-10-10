"""Synthetic surface evidence at the real recognizer seam, not live VLM accuracy."""
import json

import pytest

from .test_photo_part_contract import run_photo, feature
from app.museum.photo_policy import add_number_clues
from app.museum.surface_verification import parse_surface, decide_surface, surface_messages
from app.museum.config import MuseumSettings


def surface(part, relation='visible_conflict', query='underside', reference='front', same=True):
    return dict(**feature(part, relation), query_surface=query,
                reference_surface=reference, same_region=same)


@pytest.mark.asyncio
@pytest.mark.parametrize('query,reference,same', [
    ('underside','front',True), ('back','front',True),
    ('unknown','unknown',True), ('front','front',False),
])
async def test_noncorresponding_surfaces_neither_veto_nor_confirm(query,reference,same):
    features=[surface('decoration',query=query,reference=reference,same=same),
              surface('outline','visible_match',query,reference,same)]
    result,trace=await run_photo(features,mode='surface')
    assert trace['error'] is None
    assert result['candidates']==[] and result['identity_confirmed'] is False
    assert trace['comparison_summary'][0]['different_parts']==[]
    assert trace['comparison_summary'][0]['matching_parts']==[]
    assert trace['comparison_summary'][0]['reason']!='visible_difference'
    assert all(r['state']=='cannot_compare' for r in trace['visibility_summary'][0]['regions'])
    assert 'fixture query detail' not in json.dumps(trace)


@pytest.mark.asyncio
async def test_same_surface_distinctive_matches_remain_candidates():
    features=[surface(p,'visible_match','front','front') for p in ['top','base']]
    result,trace=await run_photo(features,mode='surface')
    assert result['match_state']=='likely_match' and trace['error'] is None
    assert result['identity_confirmed'] is False


@pytest.mark.asyncio
async def test_real_same_region_conflict_is_not_erased_by_other_matches():
    features=[surface('top','visible_match','front','front'),
              surface('base','visible_conflict','front','front')]
    result,trace=await run_photo(features,mode='surface')
    assert not result['candidates'] and trace['error'] is None
    assert trace['comparison_summary'][0]['different_parts']==['base']
    assert trace['comparison_summary'][0]['reason']=='visible_difference'


@pytest.mark.asyncio
async def test_missing_surface_schema_fails_closed_without_retry():
    result,trace=await run_photo([feature('top')],mode='surface')
    assert result['status']=='service_unavailable'
    assert trace['error']=='ValidationError'


@pytest.mark.asyncio
async def test_lookalike_label_gate_survives_surface_matches():
    features=[surface(p,'visible_match','front','front') for p in ['top','base']]
    result,trace=await run_photo(features,mode='surface',label_gate=True)
    assert not result['candidates'] and trace['error'] is None
    assert trace['comparison_summary'][0]['reason']=='lookalike_requires_label'


@pytest.mark.parametrize('identity', ['same_work','different_work'])
def test_unsupported_global_identity_is_downgraded_even_with_exact_number(identity):
    row=dict(candidate_id='plate',identity=identity,
             features=[surface('decoration'),surface('outline','visible_match')])
    parsed=parse_surface({'comparisons':[row]},['plate'])
    source=dict(_id='plate',title='Fixture',fields={'accession_number':'C.929-1922'})
    result,summary,audit=decide_surface(parsed,[source],[{'source_id':'plate','score':.9}],'C.929-1922')
    assert not result['candidates'] and summary[0]['reason']=='identity_evidence_insufficient'
    assert summary[0]['unsupported_match_removed'] == (identity=='same_work')
    assert summary[0]['unsupported_veto_removed'] == (identity=='different_work')
    assert all(r['surface_guard_applied'] for r in audit[0]['regions'])
    add_number_clues(result,[source],['plate'])
    assert result['number_candidates'][0]['id']=='plate' and not result['identity_confirmed']


@pytest.mark.parametrize('ids', [[],['other'],['plate','plate'],['plate','other']])
def test_provider_must_cover_exact_reference_set(ids):
    response={'comparisons':[dict(candidate_id=s,identity='uncertain',features=[]) for s in ids]}
    with pytest.raises(ValueError):
        parse_surface(response,['plate'])


def test_defaults_and_ab_keep_old_production_and_identical_image_payload():
    assert MuseumSettings(_env_file=None).museum_photo_verification=='legacy'
    original=[{'role':'system','content':'old protocol'},
              {'role':'user','content':[{'type':'text','text':'frozen fixture'}]}]
    changed=surface_messages(original)
    assert changed[1]==original[1] and original[0]['content']=='old protocol'
    assert changed[0]!=original[0]
