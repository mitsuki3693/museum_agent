"""A text-recalled work must receive its image even when visual Top 3 missed it."""
import json
from types import SimpleNamespace
import pytest
from app.storage.store import MemoryStore
from app.museum.vision import PhotoRecognizer
from app.museum.photo_policy import Comparisons,decide
from .test_museum_visual_retrieval import image_bytes,gallery,ColorEncoder
from app.museum.visual_index import MuseumVisualIndex

@pytest.mark.asyncio
async def test_text_recall_rescues_actual_reference_and_records_provenance():
    rows=[{'_id':sid,'title':sid,'content':sid,'status':'active','source_hash':'v1'} for sid in ['wrong','flower']]
    store=MemoryStore()
    for row in rows:await store.upsert('museum_sources',row)
    class Text:
        records={r['_id']:r for r in rows}
        async def search(self,query):return [rows[1]]
    class Visual:
        index_hash='test'
        async def search(self,raw):return [{'source_id':'wrong','reference_id':'wrong-image','score':.9}]
        def reference_hits(self,ids):return [{'source_id':'flower','reference_id':'flower-image'}] if 'flower' in ids else []
        def reference_image(self,hit):return image_bytes()
    class Client:
        async def complete_json(self,messages):
            if '观察这张照片' in str(messages):return {'usable':True,'visible_text':'','visual_description':'flower pyramid'}
            assert '馆藏参考图，候选 id：flower' in str(messages), 'Text candidate had no image to compare'
            return {'comparisons':[{'candidate_id':'flower','identity':'same_work','features':[
                {'part':part,'query_detail':'distinct detail','reference_detail':'distinct detail','relation':'match','distinctive':True} for part in ['top','base']],
                'shared_features':['blue_white','tiered'],'needs':[]}]}
    engine=SimpleNamespace(store=store,index=Text(),visual_index=Visual(),client_factory=Client)
    result=await PhotoRecognizer(engine).recognize(image_bytes(),{'_id':'session'})
    assert [c['id'] for c in result['candidates']]==['flower']
    trace=await store.get('museum_photo_traces',result['trace_id'])
    assert trace['visual_retrieved_ids']==['wrong']
    assert trace['reference_rescued_ids']==['flower']
    assert trace['comparison_reference_ids']==['wrong','flower']

@pytest.mark.asyncio
async def test_reference_rescue_is_deduplicated_bounded_and_prefers_whole_view(tmp_path):
    manifest,records=gallery(tmp_path)
    data=json.loads(manifest.read_text())
    data['references'][1]['view']='whole'
    manifest.write_text(json.dumps(data))
    index=MuseumVisualIndex(manifest,tmp_path,records,encoder=ColorEncoder());await index.start()
    hits=index.reference_hits(['unknown','red','red','blue'])
    assert [h['source_id'] for h in hits]==['red','blue']
    assert hits[0]['reference_id']=='red-back'
    assert all('score' not in h for h in hits)

def test_generic_shape_alone_does_not_outrank_meaningful_ceramic_similarity():
    sources=[{'_id':sid,'title':sid} for sid in ['statue','ceramic']]
    rows=[{'candidate_id':'statue','identity':'different_work','shared_features':['outline']},
          {'candidate_id':'ceramic','identity':'uncertain','shared_features':['blue_white','tiered','spouts']}]
    result,_=decide(Comparisons.model_validate({'comparisons':rows}),sources,[{'source_id':'statue','score':.9},{'source_id':'ceramic','score':.5}], '')
    assert result['similar_candidates'][0]['id']=='ceramic'

def test_unknown_optional_display_tags_are_dropped_but_identity_stays_strict():
    payload={'comparisons':[{'candidate_id':'statue','identity':'uncertain','shared_features':['figures','unknown_tag'],'needs':['label','unknown_step']}]}
    parsed=Comparisons.model_validate(payload)
    assert parsed.comparisons[0].shared_features==['figures']
    assert parsed.comparisons[0].needs==['label']
    payload['comparisons'][0]['identity']='unknown_identity'
    with pytest.raises(ValueError):Comparisons.model_validate(payload)

def test_photo_export_preserves_reference_and_policy_versions_without_private_payload():
    from app.museum.runtime import export_metrics
    row={'_id':'private-trace','session_id':'private-session','created_at':1,
         'policy_version':'test-policy','comparison_reference_ids':['flower'],
         'reference_rescued_ids':['flower'],'photo_hash':'private-hash',
         'visible_text':'private-ocr','photo':'private-photo'}
    result=export_metrics({'traces':[],'photo_traces':[row],'feedback':[]})
    exported=result['rows'][0]
    assert exported['policy_version']=='test-policy'
    assert exported['comparison_reference_ids']==['flower']
    assert exported['reference_rescued_ids']==['flower']
    assert 'private-' not in json.dumps(result)
