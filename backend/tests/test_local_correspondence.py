import io
from types import SimpleNamespace
import numpy as np
from PIL import Image
from app.museum.local_correspondence import patch_input, correspond

def test_letterbox_keeps_tall_image_and_maps_to_original_coordinates():
    raw=io.BytesIO(); Image.new("RGB", (70,280),"blue").save(raw,format="PNG")
    square,coords,valid,cell=patch_input(raw.getvalue())
    assert square.size==(224,224) and valid.sum()==64
    assert np.all((coords[valid]>=0)&(coords[valid]<=1))
    assert coords[valid][:,1].min()<.04 and coords[valid][:,1].max()>.96
    _,_,roi_valid,_=patch_input(raw.getvalue(),[0,0,1,.5])
    assert roi_valid.sum()==32

def test_distinctive_local_region_maps_without_requiring_whole_reference_coverage():
    rng=np.random.default_rng(10)
    q=rng.normal(size=(256,384));q/=np.linalg.norm(q,axis=1,keepdims=True)
    r=rng.normal(size=(256,384));r/=np.linalg.norm(r,axis=1,keepdims=True)
    y,x=np.mgrid[:16,:16];coords=np.stack([(x+.5)/16,(y+.5)/16],axis=-1).reshape(-1,2)
    mask=np.zeros(256,bool);mask[:64]=True
    r[128:192]=q[:64]  # Query's visible region corresponds to another part of the reference.
    result=correspond(q,r,coords,coords,mask,np.ones(256,bool))
    assert result["coverage"]==1 and result["match_count"]==64
    assert all(p["reference"][1]>p["query"][1] for p in result["pairs"])

def test_repeated_texture_does_not_generate_false_coverage():
    q=np.ones((256,384))/np.sqrt(384)
    y,x=np.mgrid[:16,:16];coords=np.stack([(x+.5)/16,(y+.5)/16],axis=-1).reshape(-1,2)
    result=correspond(q,q,coords,coords,np.ones(256,bool),np.ones(256,bool))
    assert result["coverage"]==0 and not result["pairs"]

def test_patch_cache_reuses_reference_only_and_rebuilds_wrong_key(tmp_path):
    from app.museum.local_correspondence import LocalCorrespondence
    raw=io.BytesIO();Image.new('RGB',(224,224),'blue').save(raw,format='JPEG')
    class Encoder:
        calls=0
        def encode_patches(self,image):
            self.calls+=1
            return np.eye(256,384,dtype=np.float32)
    encoder=Encoder();visual=SimpleNamespace(images={'ref':raw.getvalue()},encoder=encoder)
    local=LocalCorrespondence(visual,tmp_path);first=local._encode_reference('ref')[0]
    assert encoder.calls==1
    warm=LocalCorrespondence(visual,tmp_path)
    np.testing.assert_array_equal(warm._encode_reference('ref')[0],first)
    assert encoder.calls==1 and warm.cache_stats['hits']==1
    file=next((tmp_path/'patches-v1').glob('*.npz'))
    with np.load(file,allow_pickle=False) as saved:
        np.savez(tmp_path/'wrong.npz',vectors=saved['vectors'],key='wrong-source',digest=saved['digest'])
    file.write_bytes((tmp_path/'wrong.npz').read_bytes())
    local=LocalCorrespondence(visual,tmp_path);local._encode_reference('ref')
    assert encoder.calls==2
