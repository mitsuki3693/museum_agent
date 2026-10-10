"""Local, frozen crop-before-feature ablation. Never contacts a model API.

prepare: isolated OpenCV environment; propose boxes for all gallery and queries.
evaluate: serving environment; same DINOv2, five crops and max aggregation as A.
"""
import argparse
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
import hashlib
import io
import json
from pathlib import Path
import time

import numpy as np
from PIL import Image, ImageOps, ImageDraw
from foreground_crop import VERSION, PARAMETERS, propose_box, crop_image

ROOT = Path(__file__).resolve().parents[1]
CAPTURE = ROOT/'eval/private/visual-aggregation-v1/capture.json'
GALLERY = ROOT/'data/private/va-pilot-1000-v1-references.json'


def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()


def load(p): return json.loads(p.read_bytes())


def write_new(p, value):
    with p.open('x', encoding='utf-8') as f:
        json.dump(value, f, ensure_ascii=False, indent=2)


def clean_image(path):
    # Exactly mirror prepare_image without importing serving-only dependencies.
    with Image.open(path) as source:
        source.load()
        image = ImageOps.exif_transpose(source).convert('RGB')
    image.thumbnail((1600, 1600))
    buffer = io.BytesIO(); image.save(buffer, format='JPEG', quality=85)
    return buffer.getvalue()


def prepare_case(row):
    path = ROOT/row['path']; assert sha(path) == row['sha256']
    raw = clean_image(path)
    with Image.open(io.BytesIO(raw)) as image:
        started = time.perf_counter(); proposal = propose_box(image)
    return {**row, 'clean_sha256': hashlib.sha256(raw).hexdigest(),
            'proposal': proposal, 'ms': round((time.perf_counter()-started)*1000, 3)}


def prepare(folder):
    import cv2
    capture = load(CAPTURE)
    assert capture['complete'] and sha(GALLERY) == capture['gallery_sha256']
    folder.mkdir(parents=True, exist_ok=False)
    plan = dict(version=VERSION, parameters=PARAMETERS, capture_sha256=sha(CAPTURE),
        gallery_sha256=sha(GALLERY), cv2_version=cv2.__version__, api_calls=0,
        variants=['A:original query/original gallery', 'B:query crop/original gallery',
                  'C:query crop/gallery crop'],
        acceptance='No loss of original in-corpus Top1/Top5; no activation without visual and identity review',
        human_reviewed=False, preprocessing_workers=4)
    write_new(folder/'plan.json', plan)
    cases = [dict(key=r['id'], kind='query', path=r['path'], sha256=r['sha256']) for r in capture['rows']]
    cases += [dict(key=r['id'], kind='reference', path=str((GALLERY.parent/r['path']).relative_to(ROOT)),
                   sha256=r['sha256']) for r in load(GALLERY)['references']]
    proposals = []
    with (folder/'boxes.jsonl').open('x', encoding='utf-8') as stream, ProcessPoolExecutor(max_workers=4) as pool:
        for i, record in enumerate(pool.map(prepare_case, cases, chunksize=1)):
            proposals.append(record); stream.write(json.dumps(record, ensure_ascii=False)+'\n'); stream.flush()
            if i % 100 == 0: print('boxes', i, '/', len(cases), flush=True)
    counts = {kind: dict(Counter(r['proposal']['reason'] for r in proposals if r['kind']==kind))
              for kind in ['query', 'reference']}
    write_new(folder/'boxes.complete.json', dict(count=len(cases), boxes_sha256=sha(folder/'boxes.jsonl'),
                                                plan_sha256=sha(folder/'plan.json'), reasons=counts))
    print(json.dumps(counts), flush=True)
    preview(folder)


def preview(folder):
    # Contact sheets expose all 42 query crop decisions, including fallback.
    proposals = [json.loads(line) for line in (folder/'boxes.jsonl').read_text(encoding='utf-8').splitlines()]
    queries = [r for r in proposals if r['kind']=='query']
    for start in range(0, len(queries), 12):
        page = queries[start:start+12]
        sheet = Image.new('RGB', (1200, 310*((len(page)+3)//4)), '#eeeeee')
        draw = ImageDraw.Draw(sheet)
        for n, r in enumerate(page):
            x, y = (n % 4)*300, (n//4)*310
            with Image.open(io.BytesIO(clean_image(ROOT/r['path']))) as image:
                full = image.convert('RGB')
            preview = ImageOps.contain(full, (292, 258))
            px, py = x+(292-preview.width)//2, y+20
            sheet.paste(preview, (px, py))
            if r['proposal']['applied']:
                a,b,c,d = r['proposal']['box']
                draw.rectangle((px+a*preview.width,py+b*preview.height,
                                px+c*preview.width,py+d*preview.height), outline='red', width=3)
            draw.text((x+3,y+3), r['key'], fill='black')
            draw.text((x+3,y+280), r['proposal']['reason'][:36], fill='black')
        sheet.save(folder/f'queries-{start:02}.jpg')


def evaluate(folder):
    import torch
    from app.museum.config import MuseumSettings
    from app.museum.visual_index import MuseumVisualIndex, DinoEncoder, MODEL_REVISION, image_views
    from app.museum.vision import prepare_image
    from evaluate_visual_aggregation import legacy_scores, rank, summary
    torch.set_num_threads(4)
    if (folder/'results.json').exists() or (folder/'results.partial.jsonl').exists():
        raise FileExistsError('Preserve evaluation evidence')
    plan, complete = load(folder/'plan.json'), load(folder/'boxes.complete.json')
    assert sha(CAPTURE) == plan['capture_sha256'] and sha(GALLERY) == plan['gallery_sha256']
    assert sha(folder/'plan.json') == complete['plan_sha256']
    assert sha(folder/'boxes.jsonl') == complete['boxes_sha256']
    boxes = [json.loads(line) for line in (folder/'boxes.jsonl').read_text(encoding='utf-8').splitlines()]
    assert len(boxes) == complete['count']
    by_key = {(r['kind'], r['key']): r for r in boxes}
    capture = load(CAPTURE)
    records = {r['_id']:r for p in [ROOT/'data/corpus.json', ROOT/'data/private/va-pilot-1000-v1-corpus.json']
               for r in load(p)}
    assert sha(ROOT/'data/corpus.json') == capture['public_corpus_sha256']
    assert sha(ROOT/'data/private/va-pilot-1000-v1-corpus.json') == capture['corpus_sha256']
    cfg = MuseumSettings(_env_file=None, deepseek_api_key='')
    encoder = DinoEncoder(cfg.museum_visual_model)
    visual = MuseumVisualIndex(GALLERY, cfg.museum_visual_model, records, encoder=encoder,
        cache_dir=cfg.museum_visual_cache, cache_namespace=MODEL_REVISION)
    visual._start()
    assert visual.entries == capture['entries'] and visual.index_hash == capture['index_hash']
    print('Frozen gallery ready', flush=True)
    def encode_crop(raw, row):
        assert hashlib.sha256(raw).hexdigest() == row['clean_sha256']
        with Image.open(io.BytesIO(raw)) as source:
            image = crop_image(source.convert('RGB'), row['proposal'])
        w,h = image.size
        # Same five PIL crops as image_views; no second JPEG re-encoding.
        crops = [(0,0,w,h),(0,0,w,max(1,int(h*.65))),(0,int(h*.35),w,h),
                 (0,0,max(1,int(w*.65)),h),(int(w*.35),0,w,h)]
        return encoder.encode([image.crop(box) for box in crops])
    reference_vectors = []
    for i, rid in enumerate(visual.images):
        row = by_key[('reference',rid)]
        assert sha(ROOT/row['path']) == row['sha256']
        vectors = encode_crop(visual.images[rid], row) if row['proposal']['applied'] else visual.vectors[i*5:(i+1)*5]
        reference_vectors.append(vectors)
        if i % 100 == 0: print('reference features', i, '/', len(visual.images), flush=True)
    cropped_vectors = np.concatenate(reference_vectors)
    np.save(folder/'reference-features.npy', cropped_vectors, allow_pickle=False)
    rows = []
    with (folder/'results.partial.jsonl').open('x', encoding='utf-8') as stream:
        for case in capture['rows']:
            box = by_key[('query',case['id'])]
            path = ROOT/case['path']; assert sha(path) == case['sha256']
            raw = prepare_image(path.read_bytes())
            assert raw == clean_image(path)
            before = visual._search(raw,10)
            assert before == case['baseline_hits'][:10], case['id']
            started = time.perf_counter()
            query = encode_crop(raw, box) if box['proposal']['applied'] else encoder.encode(image_views(raw))
            feature_ms = round((time.perf_counter()-started)*1000, 3)
            b = legacy_scores(query @ visual.vectors.T, visual.entries)
            c = legacy_scores(query @ cropped_vectors.T, visual.entries)
            row = {k:case[k] for k in ['id','expected','group','in_corpus','baseline_rank']}
            row.update(b_rank=rank(b,case['expected']), c_rank=rank(c,case['expected']),
                b_hits=b[:20], c_hits=c[:20], proposal=box['proposal'],
                crop_ms=box['ms'], feature_ms=feature_ms)
            rows.append(row); stream.write(json.dumps(row)+'\n'); stream.flush()
            print(case['id'],row['baseline_rank'],'->',row['b_rank'],'->',row['c_rank'],flush=True)
    report = dict(complete=True, plan_sha256=sha(folder/'plan.json'),
        boxes_sha256=sha(folder/'boxes.jsonl'), capture_sha256=sha(CAPTURE),
        reference_features_sha256=sha(folder/'reference-features.npy'),
        api_calls=0, human_reviewed=False, live_enabled=False, rows=rows,
        baseline=summary(rows,'baseline_rank'), query_crop=summary(rows,'b_rank'), both_crop=summary(rows,'c_rank'))
    write_new(folder/'results.json', report)
    print(json.dumps({k:report[k] for k in ['baseline','query_crop','both_crop']}),flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['prepare', 'evaluate', 'preview'])
    parser.add_argument('--folder', type=Path, required=True)
    args = parser.parse_args()
    if not args.folder.resolve().is_relative_to((ROOT/'eval/private').resolve()):
        raise ValueError('Only private output directories allowed')
    {'prepare':prepare, 'evaluate':evaluate, 'preview':preview}[args.mode](args.folder)
