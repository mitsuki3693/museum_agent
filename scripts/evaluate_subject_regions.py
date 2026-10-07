"""Frozen three-case diagnosis: exclude non-subject correspondences, not deployable segmentation.

Run with the isolated LightGlue Python. Region annotations and all imagery stay
private. Full-image extraction/matching is unchanged; only the correspondences
fed to geometry and ranking are filtered. No network or VLM calls.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from evaluate_local_matcher import ROOT, OUT, MANIFEST, LIGHTGLUE_COMMIT, sha, save, rank, rerank


def inside(point, polygon):
    """Ray crossing for normalized, non-self-intersecting annotation polygons."""
    x, y = point
    result = False
    for a, b in zip(polygon, polygon[1:]+polygon[:1]):
        if (a[1] > y) != (b[1] > y):
            crossing = a[0] + (y-a[1])*(b[0]-a[0])/(b[1]-a[1])
            if x < crossing:
                result = not result
    return result


def subject_pair(q, r, qsize, rsize, qpolygon, rpolygon):
    return inside([q[0]/qsize[0], q[1]/qsize[1]], qpolygon) and inside(
        [r[0]/rsize[0], r[1]/rsize[1]], rpolygon)


def main():
    import cv2
    import numpy as np
    import torch
    from PIL import Image, ImageOps

    output = OUT/'subject-regions-v1.json'
    if output.exists():
        raise FileExistsError('Preserve prior diagnostic evidence')
    annotations = OUT/'subject-regions-v1.annotations.json'
    ann = json.loads(annotations.read_bytes())
    baseline = json.loads((OUT/'baseline.json').read_bytes())
    previous = json.loads((OUT/'local.json').read_bytes())
    assert ann['frozen'] and ann['human_reviewed'] is False
    assert ann['baseline_sha256'] == sha(OUT/'baseline.json')
    assert ann['local_sha256'] == sha(OUT/'local.json')
    assert baseline['complete'] and previous['complete']
    assert baseline['manifest_sha256'] == sha(MANIFEST)
    assert baseline['gallery_sha256'] == sha(ROOT/baseline['gallery_path'])
    checkout = ROOT/'.runtime/LightGlue'
    revision = subprocess.check_output([str(ROOT/'.runtime/tools/mingit/cmd/git.exe'),
        '-C', str(checkout), 'rev-parse', 'HEAD'], text=True).strip()
    assert revision == LIGHTGLUE_COMMIT
    weights = {p.name:sha(p) for p in (ROOT/'.runtime/lightglue-models/hub/checkpoints').glob('*.pth')}
    assert weights == previous['model_sha256']
    sys.path.insert(0, str(checkout))
    from lightglue import ALIKED, LightGlue
    os.environ['TORCH_HOME'] = str(ROOT/'.runtime/lightglue-models')
    torch.set_num_threads(4); torch.manual_seed(17)
    extractor = ALIKED(max_num_keypoints=1024).eval()
    matcher = LightGlue(features='aliked', depth_confidence=.95, width_confidence=.99).eval()
    cache = {}

    def features(path, digest):
        if path not in cache:
            assert sha(ROOT/path) == digest
            with Image.open(ROOT/path) as im:
                arr = np.array(ImageOps.exif_transpose(im).convert('RGB'), copy=True)
            cache[path] = extractor.extract(torch.from_numpy(arr).permute(2,0,1).float()/255, resize=768)
        return cache[path]

    def geometry(a, b, size):
        keep = np.zeros(len(a), dtype=bool)
        if len(a) >= 8:
            cv2.setRNGSeed(17)
            _, mask = cv2.findHomography(a,b,cv2.RANSAC,4.0,maxIters=2000,confidence=.995)
            if mask is not None:
                keep = mask.ravel().astype(bool)
        cells = np.clip((a[keep]/size*4).astype(int),0,3)
        return dict(matches=len(a), inliers=int(keep.sum()),
            query_grid_cells=len({tuple(c) for c in cells}),
            pairs=[dict(query=x.tolist(),reference=y.tolist()) for x,y in zip(a[keep],b[keep])])

    report = dict(scope='Selected post-hoc diagnosis, agent-drawn subject polygons; no automatic segmentation or identity decisions',
        annotations_sha256=sha(annotations), baseline_sha256=sha(OUT/'baseline.json'),
        local_sha256=sha(OUT/'local.json'), lightglue_commit=revision, model_sha256=weights,
        rule='Original full-image features and matches; require both points inside fixed subject polygons before RANSAC; same original-image grid and ranking thresholds',
        rows=[], complete=False)
    with torch.inference_mode():
        for case_id in ann['case_ids']:
            started=time.perf_counter()
            case=next(r for r in baseline['rows'] if r['id']==case_id)
            old=next(r for r in previous['rows'] if r['id']==case_id)
            q=features(case['query_path'],case['query_sha256'])
            qsize=q['image_size'][0].cpu().numpy()
            qp=q['keypoints'][0].cpu().numpy()
            qpoly=ann['polygons'][Path(case['query_path']).stem]
            full,subject=[],[]
            for hit in case['hits'][:5]:
                r=features(hit['reference_path'],hit['reference_sha256'])
                rp=r['keypoints'][0].cpu().numpy(); rsize=r['image_size'][0].cpu().numpy()
                pair=matcher({'image0':q,'image1':r})['matches'][0].cpu().numpy()
                a=qp[pair[:,0]]; b=rp[pair[:,1]]
                whole=geometry(a,b,qsize)
                prior=next(e for e in old['evidence'] if e['source_id']==hit['source_id'])
                assert all(whole[k]==prior[k] for k in ('matches','inliers','query_grid_cells')), 'Control replay drifted'
                rpoly=ann['polygons'][Path(hit['reference_path']).stem]
                mask=np.array([subject_pair(x,y,qsize,rsize,qpoly,rpoly) for x,y in zip(a,b)],dtype=bool)
                filtered=geometry(a[mask],b[mask],qsize)
                for target,ev in ((full,whole),(subject,filtered)):
                    target.append(dict(source_id=hit['source_id'],reference_id=hit['reference_id'],**ev))
            ids=[h['source_id'] for h in rerank(case['hits'][:5],subject)]
            report['rows'].append(dict(id=case_id,expected=case['expected'],
                dino_rank=case['rank'],whole_rank=old['after_rank'],subject_rank=rank(ids,case['expected']),
                reranked_ids=ids,full=full,subject=subject,elapsed_ms=round((time.perf_counter()-started)*1000,2)))
            save(output,report)
            print(case_id, 'DINO / whole / subject:',case['rank'],old['after_rank'],report['rows'][-1]['subject_rank'],flush=True)
    report['complete']=True;save(output,report)


if __name__=='__main__': main()
