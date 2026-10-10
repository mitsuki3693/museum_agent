from types import SimpleNamespace

from app.museum.collection_images import image_availability


def test_licence_and_file_must_both_allow_display(tmp_path):
    cfg=SimpleNamespace(museum_private_corpus=None)
    record={'_id':'artic-1'}
    manifest={'artic-1':{'is_public_domain':True,'image_id':'abc'}}
    assert image_availability(cfg,record,manifest,public_root=tmp_path)['image_reason']=='local_file_missing'
    (tmp_path/'artic-1.jpg').write_bytes(b'synthetic')
    assert image_availability(cfg,record,manifest,public_root=tmp_path)['image_status']=='available'
    manifest['artic-1']['is_public_domain']=False
    result=image_availability(cfg,record,manifest,public_root=tmp_path)
    assert result['image_url'] is None and result['image_reason']=='not_public_domain'


def test_private_missing_or_escape_never_advertises_broken_url(tmp_path):
    cfg=SimpleNamespace(museum_private_corpus=tmp_path/'corpus.json')
    for local in ['missing.jpg','../outside.jpg']:
        result=image_availability(cfg,{'_id':'va-1','local_image':local},{})
        assert result['image_status']=='missing' and result['image_url'] is None


def test_absent_manifest_is_explicit(tmp_path):
    result=image_availability(SimpleNamespace(museum_private_corpus=None),{'_id':'artic-2'},{})
    assert result['image_status']=='missing' and result['image_reason']=='not_in_image_manifest'
