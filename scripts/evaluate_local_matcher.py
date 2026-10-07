"""Offline image-pair ablation only. Does not call the VLM or change app settings.

baseline: run with the application's Python environment.
local: run in the isolated LightGlue environment. Requires the pinned official
checkout under .runtime/LightGlue, not an installation in the serving environment.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'eval/private/photo-stage-30-v1'
MANIFEST = ROOT/'data/private/photo-stage-30-v1/manifest.json'
LIGHTGLUE_COMMIT = 'eb42fee2d71449efb0aa5c10549752b5d75384d8'


def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)


def rank(ids, expected): return ids.index(expected)+1 if expected in ids else None


def rerank(hits, evidence):
    """Experimental visual support order, never an identity/abstention classifier.

    Only spatially distributed homography support can move a candidate. With no
    eligible support keep the original order. Fixed before inspecting results.
    """
    by_id = {r['source_id']: r for r in evidence}
    def key(hit):
        ev = by_id.get(hit['source_id'], {})
        eligible = ev.get('inliers', 0) >= 12 and ev.get('query_grid_cells', 0) >= 4
        return -(ev.get('inliers', 0) if eligible else 0)
    return sorted(hits, key=key)


async def baseline():
    import asyncio
    from app.museum.config import MuseumSettings
    from app.museum.retrieval import MuseumIndex
    from app.museum.visual_index import MuseumVisualIndex, MODEL_REVISION
    from app.storage.store import MemoryStore
    from app.museum.vision import prepare_image
    path = OUT/'baseline.json'
    if path.exists(): raise FileExistsError('Do not overwrite baseline evidence')
    manifest = json.loads(MANIFEST.read_bytes())
    if not manifest.get('selection_frozen'): raise ValueError('Freeze reviewed selection first')
    cfg = MuseumSettings()
    gallery = cfg.museum_visual_manifest.resolve()
    if manifest['gallery_sha256'] != sha(gallery): raise ValueError('Gallery differs from selection freeze')
    index = MuseumIndex(cfg, MemoryStore()); await index.start()
    visual = MuseumVisualIndex(cfg.museum_visual_manifest, cfg.museum_visual_model, index.records, cache_dir=cfg.museum_visual_cache)
    await visual.start()
    refs = json.loads(gallery.read_bytes())['references']
    report = dict(scope='Independent-view development subset; not blind accuracy or identity decisions',
        manifest_sha256=sha(MANIFEST), model_revision=MODEL_REVISION,
        gallery_path=str(gallery.relative_to(ROOT)), gallery_sha256=sha(gallery),
        corpus_hash=index.corpus_hash, index_hash=visual.index_hash, rows=[], complete=False)
    OUT.mkdir(parents=True, exist_ok=True)
    for case in manifest['cases']:
        query_path = ROOT/case['path']
        if sha(query_path) != case['sha256']: raise ValueError('Query changed')
        start = time.perf_counter()
        hits = await visual.search(prepare_image(query_path.read_bytes()), top_k=10)
        elapsed = (time.perf_counter()-start)*1000
        for h in hits:
            ref = next(r for r in refs if r['source_id']==h['source_id'] and r['id']==h['reference_id'])
            rp = gallery.parent/ref['path']
            h.update(reference_path=str(rp.relative_to(ROOT)), reference_sha256=sha(rp))
        report['rows'].append(dict(id=case['id'], query_path=case['path'], query_sha256=case['sha256'],
            expected=case['expected_source_id'], group=case['group'], rank=rank([h['source_id'] for h in hits],case['expected_source_id']),
            retrieval_ms=round(elapsed,2), hits=hits))
        save(path, report)
        print(case['id'], 'rank', report['rows'][-1]['rank'], flush=True)
    report['complete']=True; save(path,report)


def local():
    import cv2
    import numpy as np
    import torch
    from PIL import Image, ImageOps
    path = OUT/'local.json'
    if path.exists(): raise FileExistsError('Do not overwrite local evidence')
    checkout = ROOT/'.runtime/LightGlue'
    import subprocess
    actual = subprocess.check_output([str(ROOT/'.runtime/tools/mingit/cmd/git.exe'), '-C',str(checkout),'rev-parse','HEAD'],text=True).strip()
    if actual != LIGHTGLUE_COMMIT: raise ValueError('Pinned LightGlue source changed')
    sys.path.insert(0,str(checkout))
    from lightglue import ALIKED, LightGlue
    os.environ['TORCH_HOME'] = str(ROOT/'.runtime/lightglue-models')
    torch.set_num_threads(4); torch.manual_seed(17); cv2.setRNGSeed(17)
    extractor = ALIKED(max_num_keypoints=1024).eval()
    matcher = LightGlue(features='aliked', depth_confidence=.95, width_confidence=.99).eval()
    before = json.loads((OUT/'baseline.json').read_bytes())
    if not before.get('complete') or before['manifest_sha256'] != sha(MANIFEST): raise ValueError('Baseline not frozen')
    if before['gallery_sha256'] != sha(ROOT/before['gallery_path']): raise ValueError('Gallery changed')
    cache = {}
    def extract(relative, digest):
        p = ROOT/relative
        if sha(p) != digest: raise ValueError('Input image changed')
        with Image.open(p) as im:
            arr = np.array(ImageOps.exif_transpose(im).convert('RGB'),copy=True)
        image = torch.from_numpy(arr).permute(2,0,1).float()/255
        return extractor.extract(image,resize=768)
    report = dict(scope='Offline Top5 visual-support rerank; no identity or VLM output',
        baseline_sha256=sha(OUT/'baseline.json'), lightglue_commit=actual, torch_version=torch.__version__,
        resize=768,keypoints=1024,threads=4,shortlist=5,geometry='homography RANSAC 4px / 2000 iterations; >=12 inliers in >=4 query 4x4 cells',
        model_sha256={p.name:sha(p) for p in (ROOT/'.runtime/lightglue-models/hub/checkpoints').glob('*.pth')},
        rows=[],complete=False)
    with torch.inference_mode():
        for case in before['rows']:
            started = time.perf_counter()
            q = extract(case['query_path'],case['query_sha256'])
            evidence=[]
            for hit in case['hits'][:5]:
                t=time.perf_counter(); key=hit['reference_sha256']; cached=key in cache
                if not cached: cache[key]=extract(hit['reference_path'],key)
                r=cache[key]; pair=matcher({'image0':q,'image1':r})
                matches=pair['matches'][0].cpu().numpy()
                qp=q['keypoints'][0].cpu().numpy(); rp=r['keypoints'][0].cpu().numpy()
                a=qp[matches[:,0]]; b=rp[matches[:,1]]
                keep=np.zeros(len(matches),dtype=bool)
                if len(matches)>=8:
                    cv2.setRNGSeed(17)
                    _,mask=cv2.findHomography(a,b,cv2.RANSAC,4.0,maxIters=2000,confidence=.995)
                    if mask is not None: keep=mask.ravel().astype(bool)
                size=q['image_size'][0].cpu().numpy()
                cells=np.clip((a[keep]/size*4).astype(int),0,3)
                evidence.append(dict(source_id=hit['source_id'],reference_id=hit['reference_id'],
                    matches=len(matches),inliers=int(keep.sum()),query_grid_cells=len({tuple(c) for c in cells}),
                    query_keypoints=len(qp),reference_keypoints=len(rp),reference_cached=cached,
                    pair_ms=round((time.perf_counter()-t)*1000,2),
                    pairs=[{'query':x.tolist(),'reference':y.tolist()} for x,y in zip(a[keep][:40],b[keep][:40])]))
            ordered=rerank(case['hits'][:5],evidence)+case['hits'][5:]
            ids=[h['source_id'] for h in ordered]
            report['rows'].append(dict(id=case['id'],expected=case['expected'],group=case['group'],
                before_rank=case['rank'],after_rank=rank(ids,case['expected']),reranked_ids=ids,evidence=evidence,
                local_ms=round((time.perf_counter()-started)*1000,2)))
            save(path,report)
            print(case['id'],case['rank'],'->',report['rows'][-1]['after_rank'],flush=True)
    report['complete']=True;save(path,report)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('mode',choices=['baseline','local'])
    if parser.parse_args().mode=='baseline':
        import asyncio
        asyncio.run(baseline())
    else: local()
