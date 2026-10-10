"""Frozen local photo retrieval experiment; no model API or live config changes.

Capture the actual production search plus its per-crop similarities once, then
compare aggregation without changing images, features, or candidate coverage.
Private score matrices contain derived image data and must never be published.
"""
import argparse
import hashlib
import json
from pathlib import Path
import time

import numpy as np

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_new(path, value):
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def rank(hits, expected):
    return next((i for i, hit in enumerate(hits, 1) if hit['source_id'] == expected), None)


def legacy_scores(similarities, entries):
    by_source = {}
    for entry, score in zip(entries, similarities.max(axis=0), strict=True):
        old = by_source.get(entry['source_id'])
        if old is None or score > old['score']:
            by_source[entry['source_id']] = {**entry, 'score': round(float(score), 6)}
    return sorted(by_source.values(), key=lambda h: (-h['score'], h['source_id']))


def query_coverage_scores(similarities, entries):
    """One-way crop coverage, not pixel foreground coverage or identity confidence.

    All five query views must find support in the SAME reference photograph.
    Missing reference regions are not penalised; extra reference views do not
    accumulate votes. The five crops and arithmetic mean are fixed a priori.
    """
    by_reference = {}
    for i, entry in enumerate(entries):
        by_reference.setdefault((entry['source_id'], entry['reference_id']), []).append(i)
    by_source = {}
    for (sid, rid), columns in by_reference.items():
        support = similarities[:, columns].max(axis=1)
        score = round(float(support.mean()), 6)
        hit = dict(source_id=sid, reference_id=rid, score=score,
                   query_support=[round(float(x), 6) for x in support])
        old = by_source.get(sid)
        if old is None or score > old['score']:
            by_source[sid] = hit
    return sorted(by_source.values(), key=lambda h: (-h['score'], h['source_id']))


def summary(rows, key):
    result = {}
    for name, subset in [('stage30', [r for r in rows if r['group'] != 'legacy']),
                         ('legacy12', [r for r in rows if r['group'] == 'legacy'])]:
        positives = [r for r in subset if r['in_corpus']]
        ranks = [r[key] for r in positives]
        result[name] = {'in_corpus': len(positives), 'out_of_corpus': len(subset)-len(positives),
                       **{f'top{k}': sum(r is not None and r <= k for r in ranks) for k in [1, 2, 3, 5, 10, 20]},
                       'mrr': round(sum(1/r if r else 0 for r in ranks)/len(ranks), 6)}
    return result


def compare(folder):
    frozen = folder/'capture.json'
    data = json.loads(frozen.read_bytes())
    assert data['complete']
    out = folder/'query-coverage.json'
    if out.exists():
        raise FileExistsError('Do not overwrite experiment results')
    report = dict(version='query-crop-coverage-mean-v1', capture_sha256=sha(frozen),
                  variable='max(query crops) -> mean(query crops); references and vectors unchanged',
                  api_calls=0, live_enabled=False, rows=[])
    for case in data['rows']:
        path = folder/case['matrix']
        assert sha(path) == case['matrix_sha256']
        sim = np.load(path, allow_pickle=False)
        assert sim.shape == (5, len(data['entries'])) and np.isfinite(sim).all()
        assert legacy_scores(sim, data['entries']) == case['baseline_hits']
        hits = query_coverage_scores(sim, data['entries'])
        row = {key: case[key] for key in ['id', 'expected', 'group', 'in_corpus', 'baseline_rank']}
        row.update(candidate_rank=rank(hits, case['expected']), candidate_hits=hits[:20])
        report['rows'].append(row)
        if case['in_corpus'] and row['candidate_rank'] != row['baseline_rank']:
            print(case['id'], row['baseline_rank'], '->', row['candidate_rank'], flush=True)
    report['baseline'] = summary(report['rows'], 'baseline_rank')
    report['candidate'] = summary(report['rows'], 'candidate_rank')
    report['complete'] = True
    write_new(out, report)
    print(json.dumps({k: report[k] for k in ['baseline', 'candidate']}))


def diagnose(folder):
    """Render actual winning crop pairs; no inferred foreground/identity labels."""
    from PIL import Image, ImageDraw, ImageOps
    from app.museum.visual_index import image_views
    from app.museum.vision import prepare_image

    data = json.loads((folder/'capture.json').read_bytes())
    gallery = ROOT/'data/private/va-pilot-1000-v1-references.json'
    assert sha(gallery) == data['gallery_sha256']
    references = {r['id']: r for r in json.loads(gallery.read_bytes())['references']}
    names = ['whole-center', 'top-center', 'bottom-center', 'left-center', 'right-center']
    report = []

    def encoded_area(image):
        w, h = image.size
        if max(w, h) > 8*min(w, h):
            side = min(w, h)
            x, y = (w-side)//2, (h-side)//2
            image = image.crop((x, y, x+side, y+side))
            w, h = image.size
        image = image.resize((int(w*256/min(w, h)), int(h*256/min(w, h))), Image.Resampling.BICUBIC)
        x, y = (image.width-224)//2, (image.height-224)//2
        return image.crop((x, y, x+224, y+224))

    for case in data['rows']:
        if case['id'] not in {'legacy-04', 'legacy-08', 'va-2017KD6167', 'va-2020MP1936', 'va-2007BM5078'}:
            continue
        path = ROOT/case['path']
        assert sha(path) == case['sha256']
        qviews = image_views(prepare_image(path.read_bytes()))
        sim_path = folder/case['matrix']
        assert sha(sim_path) == case['matrix_sha256']
        sim = np.load(sim_path, allow_pickle=False)
        targets = list(dict.fromkeys([h['source_id'] for h in case['baseline_hits'][:2]] + [case['expected']]))
        sheet = Image.new('RGB', (980, 270*len(targets)), '#eeeeee')
        draw = ImageDraw.Draw(sheet)
        for row, sid in enumerate(targets):
            columns = [i for i, e in enumerate(data['entries']) if e['source_id'] == sid]
            qi, col = np.unravel_index(sim[:, columns].argmax(), (5, len(columns)))
            index = columns[col]
            entry = data['entries'][index]
            ref = references[entry['reference_id']]
            reference_path = gallery.parent/ref['path']
            assert sha(reference_path) == ref['sha256']
            rviews = image_views(prepare_image(reference_path.read_bytes()))
            item = dict(id=case['id'], source_id=sid, expected=sid == case['expected'],
                        query_crop=names[qi], reference_crop=names[index % 5],
                        score=float(sim[qi, index]), reference_id=entry['reference_id'])
            report.append(item)
            draw.text((8, 270*row+3), f"{case['id']} -> {sid} (gold={item['expected']}) {item['score']:.4f}", fill='black')
            images = [qviews[0], encoded_area(qviews[qi]), rviews[0], encoded_area(rviews[index % 5])]
            labels = ['query original', 'query '+names[qi], 'reference original', 'reference '+names[index % 5]]
            for k, (image, label) in enumerate(zip(images, labels)):
                tile = ImageOps.contain(image, (238, 224))
                sheet.paste(tile, (k*245+(238-tile.width)//2, row*270+23))
                draw.text((k*245+4, row*270+250), label, fill='black')
        sheet.save(folder/(case['id']+'-winning-crops.jpg'))
    write_new(folder/'diagnosis.json', report)
    print(json.dumps(report))


async def ocr_replay(folder):
    """Real text index with manually transcribed inputs, NOT an OCR quality test."""
    import torch
    from app.museum.config import MuseumSettings
    from app.museum.retrieval import MuseumIndex
    from app.storage.store import MemoryStore

    torch.set_num_threads(4)
    out = folder/'ocr-replay.json'
    if out.exists():
        raise FileExistsError('Preserve previous evidence')
    frozen = json.loads((folder/'capture.json').read_bytes())
    cfg = MuseumSettings().model_copy(update={'deepseek_api_key': '', 'museum_storage': 'memory',
                                             'museum_text_rerank': False})
    assert sha(cfg.museum_private_corpus) == frozen['corpus_sha256']
    assert sha(cfg.museum_corpus) == frozen['public_corpus_sha256']
    # Selected before running retrieval. Transcriptions were visually inspected;
    # C.169-1910 on the jug is not its grouped catalogue number C.169&A-1910.
    cases = [
        dict(id='plate-mark', photo='va-2007BM5078', text='C.929-1922',
             description='蓝白陶瓷盘的底部，有蓝色字母和数字标记', expected='va-o163433', exact=True),
        dict(id='jug-component-mark', photo='va-2020MP1936', text='C.169-1910',
             description='圆形的彩绘陶瓷底面，中间有编号，周围是花纹', expected='va-o161638', exact=False),
        dict(id='plate-wrong-final-digit', photo=None, text='C.929-1923',
             description='蓝白陶瓷盘', expected='va-o163433', exact=False),
        dict(id='plate-partial', photo=None, text='C.929',
             description='蓝白陶瓷盘', expected='va-o163433', exact=False),
        dict(id='caption-only-number', photo=None, text='',
             description='C.929-1922 蓝白陶瓷盘', expected='va-o163433', exact=False),
    ]
    write_new(folder/'ocr-inputs.json', {'scope': 'Agent transcription and synthetic controls, not model OCR', 'cases': cases})
    index = MuseumIndex(cfg, MemoryStore())
    await index.start()
    print('Text index loaded', len(index.records), flush=True)
    rows = []
    for case in cases:
        query = (case['text']+' '+case['description']).strip()
        before = await index.search(query)
        hits, trace = await index.search_photo_observation(case['text'], case['description'])
        before_ids, after_ids = [h['_id'] for h in before], [h['_id'] for h in hits]
        assert bool(trace['exact_ids']) == case['exact']
        if case['exact']:
            assert after_ids[0] == case['expected']
        rows.append(dict(id=case['id'], baseline_ids=before_ids, candidate_ids=after_ids, trace=trace,
                         baseline_rank=next((i for i, sid in enumerate(before_ids, 1) if sid == case['expected']), None),
                         candidate_rank=next((i for i, sid in enumerate(after_ids, 1) if sid == case['expected']), None)))
        print(case['id'], rows[-1]['baseline_rank'], '->', rows[-1]['candidate_rank'], trace['route'], flush=True)
    await index.close()
    write_new(out, dict(complete=True, api_calls=0, live_ocr_tested=False, records=len(index.records),
        input_sha256=sha(folder/'ocr-inputs.json'), capture_sha256=sha(folder/'capture.json'), rows=rows))


def capture(folder):
    import torch
    from app.museum.config import MuseumSettings
    from app.museum.visual_index import MuseumVisualIndex, DinoEncoder, MODEL_REVISION, image_views
    from app.museum.vision import prepare_image

    torch.set_num_threads(4)
    folder.mkdir(parents=True, exist_ok=False)
    cfg = MuseumSettings(_env_file=None, deepseek_api_key='')
    source = ROOT/'eval/private/scale-1000-candidate-v1.json'
    previous = json.loads(source.read_bytes())
    gallery = ROOT/'data/private/va-pilot-1000-v1-references.json'
    corpus = ROOT/'data/private/va-pilot-1000-v1-corpus.json'
    public = ROOT/'data/corpus.json'
    assert previous['complete'] and previous['gallery_sha256'] == sha(gallery)
    assert previous['corpus_sha256'] == sha(corpus) and previous['public_corpus_sha256'] == sha(public)
    records = {r['_id']: r for p in [public, corpus] for r in json.loads(p.read_bytes())}
    visual = MuseumVisualIndex(gallery, cfg.museum_visual_model, records,
        encoder=DinoEncoder(cfg.museum_visual_model), cache_dir=cfg.museum_visual_cache,
        cache_namespace=MODEL_REVISION)
    visual._start()
    print('Gallery loaded', len(visual.images), flush=True)
    report = dict(version='visual-aggregation-v1', complete=False,
        scope='Repeated development set; retrieval only, not final identity or OOD rejection',
        model_revision=MODEL_REVISION, index_hash=visual.index_hash,
        source_report_sha256=sha(source), gallery_sha256=sha(gallery),
        corpus_sha256=sha(corpus), public_corpus_sha256=sha(public),
        records=len(records), reference_images=len(visual.images),
        api_calls=0, entries=visual.entries, rows=[])
    for case in previous['photos']:
        path = ROOT/case['path']
        assert sha(path) == case['sha256']
        clean = prepare_image(path.read_bytes())
        start = time.perf_counter()
        actual = visual._search(clean, 10)
        ms = round((time.perf_counter()-start)*1000, 3)
        query = visual.encoder.encode(image_views(clean))
        similarities = query @ visual.vectors.T
        replay = legacy_scores(similarities, visual.entries)
        assert actual == replay[:10], f"Aggregation replay drift: {case['id']}"
        assert actual == case['hits'], f"Historical baseline drift: {case['id']}"
        matrix = folder/(case['id']+'.npy')
        np.save(matrix, similarities, allow_pickle=False)
        row = {key: case[key] for key in ['id', 'path', 'sha256', 'expected', 'group', 'in_corpus', 'exact_reference_overlap']}
        row.update(matrix=matrix.name, matrix_sha256=sha(matrix), baseline_hits=replay,
                   baseline_rank=rank(replay, case['expected']), search_ms=ms)
        report['rows'].append(row)
        print(case['id'], row['baseline_rank'], flush=True)
    report['complete'] = True
    write_new(folder/'capture.json', report)
    print('Complete: current production and historical Top10 agree on all 42 images', flush=True)


def check(folder):
    """Red-capable retrieval acceptance against the current fixed in-corpus set."""
    data = json.loads((folder/'capture.json').read_bytes())
    assert data['complete']
    rows = [r for r in data['rows'] if r['in_corpus']]
    failures = [r['id'] for r in rows if r['baseline_rank'] is None or r['baseline_rank'] > 5]
    print(json.dumps({'in_corpus': len(rows), 'top5_misses': failures}))
    assert not failures, 'Known partial/view queries still miss the Top5 shortlist'


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['capture', 'check', 'compare', 'diagnose', 'ocr'])
    parser.add_argument('--folder', type=Path, required=True)
    args = parser.parse_args()
    if not args.folder.resolve().is_relative_to((ROOT/'eval/private').resolve()):
        raise ValueError('Use a private evaluation directory')
    if args.mode == 'ocr':
        import asyncio
        asyncio.run(ocr_replay(args.folder))
    else:
        {'capture': capture, 'check': check, 'compare': compare, 'diagnose': diagnose}[args.mode](args.folder)
