"""Freeze the production comparison after replaying actual OCR; six calls max.

capture is offline and writes private, image-bearing messages. live sends only
these frozen messages, once per planned repeat, with no automatic retry/resume.
replay changes only the local decision policy on saved provider responses.
"""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import time
from urllib.parse import urlparse

from app.museum.config import ROOT, MuseumSettings
from app.museum.engine import MuseumEngine
from app.museum.photo_policy import Comparisons, decide, POLICY_VERSION
from app.museum.retrieval import MuseumIndex
from app.museum.vision import PhotoRecognizer
from app.museum.visual_index import MuseumVisualIndex
from app.storage.store import MemoryStore

IDS = ['va-2020MP1936', 'va-2007BM5078']
VERSION = 'bottom-verification-v1'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_new(path, value):
    with path.open('x', encoding='utf-8') as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.write('\n')


def read(path):
    return json.loads(path.read_bytes())


def output_folder(value):
    path = Path(value).resolve()
    if not path.is_relative_to((ROOT/'eval/private').resolve()):
        raise ValueError('Image-bearing experiment must stay under eval/private')
    return path


async def capture(folder):
    import torch
    torch.set_num_threads(4)
    cfg = MuseumSettings()
    old = ROOT/'eval/private/bottom-mark-ocr-v1'
    ocr_plan, ocr = read(old/'plan.json'), read(old/'results.json')
    assert ocr['complete'] and ocr['plan_sha256'] == sha(old/'plan.json')
    for name, digest in ocr_plan['files'].items():
        assert sha(Path(name)) == digest
    frozen_cfg = MuseumSettings(_env_file=None, **ocr_plan['index_config'],
        museum_photo_reference_mode='single', museum_photo_verification='legacy')
    folder.mkdir(parents=True, exist_ok=False)
    store = MemoryStore()
    index = MuseumIndex(frozen_cfg, store)
    snapshots = []
    try:
        await index.start()
        assert index.corpus_hash == ocr['corpus_hash']
        visual = MuseumVisualIndex(cfg.museum_visual_manifest, cfg.museum_visual_model,
            index.records, cache_dir=cfg.museum_visual_cache)
        await visual.start()
        engine = MuseumEngine(frozen_cfg, store, index)
        engine.visual_index = visual
        for case in ocr_plan['cases']:
            observation = next(r['observation'] for r in ocr['rows'] if r['case_id'] == case['id'] and r['repeat'] == 1)
            captured = []
            class CaptureClient:
                async def complete_json(self, messages):
                    if messages[0]['content'].startswith('只描述照片中可见'):
                        return observation
                    captured.append(messages)
                    return {'comparisons': []}  # no identity score from this local capture
            engine.client_factory = CaptureClient
            result = await PhotoRecognizer(engine).recognize(Path(case['path']).read_bytes(), {'_id':'offline-'+case['id']})
            trace = await store.get('museum_photo_traces', result['trace_id'])
            assert trace['error'] is None and len(captured) == 1
            assert case['expected'] in trace['compared_ids']
            assert case['expected'] in trace['comparison_reference_ids']
            score_map = dict(zip(trace['visual_retrieved_ids'], trace['visual_scores']))
            refs = [dict(source_id=sid, reference_id=rid, **({'score':score_map[sid]} if sid in score_map else {}))
                for sid, rid in zip(trace['comparison_reference_ids'], trace['comparison_image_ids'], strict=True)]
            sources = [{k:index.records[sid][k] for k in ['_id','title','fields','source_url']}
                       for sid in trace['compared_ids']]
            snapshot = dict(case_id=case['id'], expected=case['expected'], messages=captured[0],
                sources=sources, refs=refs, visible_text=observation['visible_text'],
                label_required_ids=sorted(visual.label_required_ids), trace=trace,
                observation_sha256=hashlib.sha256(json.dumps(observation,sort_keys=True).encode()).hexdigest())
            path = folder/(case['id']+'.json')
            write_new(path, snapshot)
            snapshots.append(dict(case_id=case['id'], path=path.name, sha256=sha(path)))
            print(json.dumps(dict(case_id=case['id'], correct_candidate_present=True,
                correct_reference_present=True, reference_rescued=case['expected'] in trace['reference_rescued_ids'],
                references=trace['comparison_image_ids'], api_calls=0)), flush=True)
        write_new(folder/'plan.json', dict(version=VERSION, model=cfg.deepseek_model,
            provider_host=urlparse(cfg.deepseek_base_url).hostname, repeats=3, max_calls=6,
            snapshots=snapshots, ocr_results_sha256=sha(old/'results.json'), corpus_hash=index.corpus_hash,
            index_hash=visual.index_hash, gallery_sha256=sha(cfg.museum_visual_manifest),
            policy_version=POLICY_VERSION, human_reviewed=False, online_ab=False,
            source_sha256={p:sha(ROOT/p) for p in ['backend/app/museum/vision.py', 'backend/app/museum/photo_policy.py',
                'scripts/evaluate_bottom_verification.py']}))
    finally:
        await index.close()


def apply_policy(response, snapshot, number_clues=False):
    comparisons = Comparisons.model_validate(response)
    result, decisions = decide(comparisons, snapshot['sources'], snapshot['refs'],
                               snapshot['visible_text'], set(snapshot['label_required_ids']))
    if number_clues:
        from app.museum.photo_policy import add_number_clues
        add_number_clues(result, snapshot['sources'], snapshot['trace']['ocr_exact_ids'])
    expected = snapshot['expected']
    decision = next((r for r in decisions if r['candidate_id'] == expected), None)
    return dict(result=result, decisions=decisions, expected_decision=decision,
        expected_candidate=expected in [r['id'] for r in result['candidates']],
        expected_similar=expected in [r['id'] for r in result['similar_candidates']],
        candidate_ids=[r['id'] for r in result['candidates']],
        number_ids=[r['id'] for r in result.get('number_candidates', [])],
        similar_ids=[r['id'] for r in result['similar_candidates']])


async def live(folder):
    plan, cfg = read(folder/'plan.json'), MuseumSettings()
    assert plan['version'] == VERSION and plan['max_calls'] == 6 and plan['repeats'] == 3
    assert [s['case_id'] for s in plan['snapshots']] == IDS
    assert cfg.deepseek_model == plan['model'] and urlparse(cfg.deepseek_base_url).hostname == plan['provider_host']
    for p, digest in plan['source_sha256'].items():
        assert sha(ROOT/p) == digest
    for spec in plan['snapshots']:
        assert sha(folder/spec['path']) == spec['sha256']
    assert cfg.deepseek_api_key
    write_new(folder/'started.json', dict(started_at=time.time(), max_calls=6))
    engine = MuseumEngine(cfg, MemoryStore(), None)
    rows = []
    for repeat in range(1,4):
        for spec in plan['snapshots']:
            snapshot = read(folder/spec['path'])
            n = len(rows)+1
            write_new(folder/f'call-{n}.json', dict(case_id=spec['case_id'], repeat=repeat, reserved_at=time.time()))
            row = dict(case_id=spec['case_id'], repeat=repeat, completed=False)
            client = engine._client()
            started = time.perf_counter()
            try:
                response = await client.complete_json(snapshot['messages'])
                row['response'] = response
                row.update(apply_policy(response, snapshot), completed=True)
            except Exception as exc:
                row['error_type'] = type(exc).__name__
            row.update(ms=round((time.perf_counter()-started)*1000), usage_records=client.usage_records)
            write_new(folder/f'row-{n}.json', row)
            rows.append(row)
            print(json.dumps({k:row.get(k) for k in ['case_id','repeat','completed','expected_candidate',
                'expected_similar','expected_decision','error_type']},ensure_ascii=False), flush=True)
    write_new(folder/'results.json', dict(complete=True, plan_sha256=sha(folder/'plan.json'),
        policy_version=POLICY_VERSION, api_calls=len(rows), rows=rows))


def replay(folder, output):
    if not output or Path(output).name != output:
        raise ValueError('Provide a new output filename inside the experiment folder')
    plan, previous = read(folder/'plan.json'), read(folder/'results.json')
    assert previous['plan_sha256'] == sha(folder/'plan.json')
    rows = []
    for row in previous['rows']:
        if 'response' not in row:
            rows.append(dict(case_id=row['case_id'], repeat=row['repeat'], skipped=True))
            continue
        spec = next(s for s in plan['snapshots'] if s['case_id'] == row['case_id'])
        assert sha(folder/spec['path']) == spec['sha256']
        rows.append(dict(case_id=row['case_id'], repeat=row['repeat'], **apply_policy(row['response'], read(folder/spec['path']), number_clues=True)))
    write_new(folder/output, dict(api_calls=0, policy_version=POLICY_VERSION,
        responses_sha256=sha(folder/'results.json'), rows=rows))
    print(json.dumps([dict(case_id=r['case_id'],repeat=r['repeat'],expected_candidate=r.get('expected_candidate'),
                         expected_similar=r.get('expected_similar'), number_ids=r.get('number_ids')) for r in rows]))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['capture','live','replay'])
    parser.add_argument('--folder', type=output_folder, default=ROOT/'eval/private/bottom-verification-v1')
    parser.add_argument('--output')
    args = parser.parse_args()
    if args.command == 'replay':
        replay(args.folder,args.output)
    else:
        asyncio.run(capture(args.folder) if args.command == 'capture' else live(args.folder))
