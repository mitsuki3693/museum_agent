from app.museum.photo_policy import Comparisons
from app.museum.partial_verification import decide_visible
from app.museum.partial_verification import VisibleComparisons, normalize_visible
import pytest
import json
from types import SimpleNamespace

def test_partial_positive_is_not_rejected_by_unsupported_different_work():
    # Reproduces the decision seam: model identity contradicts its own visible evidence.
    evidence = Comparisons.model_validate({"comparisons": [{"candidate_id": "flower", "identity": "different_work",
        "features": [{"part": "top", "relation": "match", "distinctive": True,
                      "query_detail": "same bust and collar", "reference_detail": "same bust and collar"},
                     {"part": "base", "relation": "not_visible", "distinctive": False,
                      "query_detail": "not photographed", "reference_detail": "lion supports"}]}]})
    result, _ = decide_visible(evidence, [{"_id": "flower", "title": "Flower pyramid"}],
                               [{"source_id": "flower", "score": .8}], "")
    assert result["match_state"] == "uncertain"
    assert [c["id"] for c in result["candidates"]] == ["flower"]

@pytest.mark.parametrize("visible,expected", [(False, "uncertain"), (True, "no_reliable_match")])
def test_only_both_visible_conflicts_veto(visible, expected):
    rows=VisibleComparisons.model_validate({"comparisons":[{"candidate_id":"flower","identity":"different_work","features":[
        {"part":"top","query_detail":"bust collar","reference_detail":"bust collar","relation":"visible_match","distinctive":True,"query_visible":True,"reference_visible":True},
        {"part":"base","query_detail":"base","reference_detail":"other base","relation":"visible_conflict","distinctive":False,"query_visible":visible,"reference_visible":True}]}]})
    normalized,audit=normalize_visible(rows)
    result,_=decide_visible(normalized,[{"_id":"flower","title":"flower"}],[{"source_id":"flower","score":.8}],"")
    assert result["match_state"]==expected
    assert audit[0]["regions"][1]["state"] == ("visible_conflict" if visible else "not_visible")

def test_no_conflict_without_positive_evidence_is_not_identity():
    rows=Comparisons.model_validate({"comparisons":[{"candidate_id":"flower","identity":"different_work","features":[]}]})
    result,_=decide_visible(rows,[{"_id":"flower","title":"flower"}],[{"source_id":"flower","score":.99}],"")
    assert not result["candidates"]

def test_label_gate_survives_positive_visible_matches():
    rows=Comparisons.model_validate({"comparisons":[{"candidate_id":"flower","identity":"same_work","features":[
        {"part":part,"query_detail":"specific","reference_detail":"specific","relation":"match","distinctive":True} for part in ["top","base"]]}]})
    result,_=decide_visible(rows,[{"_id":"flower","title":"flower"}],[{"source_id":"flower","score":.99}],"",{"flower"})
    assert not result["candidates"]

@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['visibility', 'candidate', 'partial'])
async def test_photo_pipeline_uses_asymmetric_rules_and_keeps_only_codes(mode):
    from app.storage.store import MemoryStore
    from app.museum.vision import PhotoRecognizer
    from .test_museum_visual_retrieval import image_bytes
    store=MemoryStore()
    source={'_id':'flower','title':'Flower','status':'active','source_hash':'v1'}
    await store.upsert('museum_sources',source)
    class Index:
        records={'flower':source}
        async def search(self, query):return []
    class Local:
        async def select(self, raw, hits):
            return hits,[dict(source_id='flower',reference_id='front',version='test',coverage=0.,
                coverage_scope='whole_image_excluding_padding',foreground_verified=False,match_count=0,reference_box=None)]
    class Visual:
        local_correspondence=Local()
        async def search(self, raw):return [dict(source_id='flower',reference_id='front',score=.8)]
        def reference_image(self,hit):return image_bytes()
    class Client:
        async def complete_json(self,messages):
            if messages[0]['content'].startswith('只描述照片'):
                return dict(usable=True,visible_text='',visual_description='private-description')
            assert sum(x['type']=='image_url' for x in messages[1]['content'])==2
            return {'comparisons':[dict(candidate_id='flower',identity='different_work',features=[
                dict(part='top',query_detail='private-matching-top',reference_detail='same top',query_visible=True,
                     reference_visible=True,relation='visible_match',distinctive=True),
                dict(part='base',query_detail='not seen',reference_detail='base',query_visible=False,
                     reference_visible=True,relation='visible_conflict',distinctive=False)])]}
    engine=SimpleNamespace(store=store,index=Index(),visual_index=Visual(),client_factory=Client,
        settings=SimpleNamespace(museum_photo_verification=mode))
    result=await PhotoRecognizer(engine).recognize(image_bytes(),{'_id':'test-session'})
    assert result['match_state']=='uncertain' and result['candidates'][0]['id']=='flower'
    trace=await store.get('museum_photo_traces',result['trace_id'])
    assert trace['comparison_summary'][0]['model_identity']=='different_work'
    assert trace['comparison_summary'][0]['unsupported_veto_removed']
    assert trace['visibility_summary'][0]['regions'][1]['state']=='not_visible'
    assert trace['prompt_version'].endswith(':'+mode)
    assert 'private-' not in json.dumps(trace) and 'data:image' not in json.dumps(trace)

@pytest.mark.asyncio
async def test_per_candidate_call_cannot_assign_another_candidate_identity():
    from app.museum.partial_verification import verify
    from .test_museum_visual_retrieval import image_bytes
    class Client:
        async def complete_json(self,messages):
            return {'comparisons':[dict(candidate_id='other',identity='uncertain',features=[])]}
    visual=SimpleNamespace(reference_image=lambda hit:image_bytes())
    with pytest.raises(ValueError,match='exactly'):
        await verify(Client(),image_bytes(),[{'_id':'flower','title':'Flower'}],
            [{'source_id':'flower','reference_id':'front'}],visual,'candidate')
