"""Frozen paired legacy verification after one-reference retrieval improvement.

Four known developer photos, shared observations, original/candidate galleries,
three repeats per arm. <=28 provider calls, no retries or production changes.
"""
import argparse
import asyncio
import hashlib
import json
import time
from pathlib import Path
from urllib.parse import urlparse

from evaluate_bottom_verification import ROOT, read, sha, write_new, output_folder
from evaluate_surface_controls import apply_response, outcome
from evaluate_neptune_scale import check_addition
from app.museum.config import MuseumSettings
from app.museum.engine import MuseumEngine
from app.museum.retrieval import MuseumIndex
from app.museum.visual_index import MuseumVisualIndex
from app.museum.vision import PhotoRecognizer
from app.storage.store import MemoryStore

VERSION = 'neptune-scale-identity-v1'
CASES = (4, 7, 8, 12)
MODES = ('original', 'back_reference')
REPEATS = 3
MAX_CALLS = 28
CODE = ['scripts/evaluate_neptune_identity.py', 'backend/app/museum/vision.py',
        'backend/app/museum/photo_policy.py', 'backend/app/museum/retrieval.py',
        'backend/app/museum/visual_index.py', 'backend/app/museum/engine.py', 'backend/app/llm/client.py']


def prepare(folder):
    cfg = MuseumSettings()
    old = read(ROOT/'eval/private/surface-controls-v1/plan.json')
    scale = read(ROOT/'eval/private/neptune-scale-reference-v1/results.json')
    original = ROOT/old['visual_manifest']
    candidate = ROOT/'data/private/neptune-scale-reference-v1-references.json'
    check_addition(read(original), read(candidate))
    assert scale['gate']['passed'] and not scale['live_enabled']
    pack = ROOT/'data/private/MUSE-test-pack-12-20261006'
    cases = []
    for number in CASES:
        entry = read(pack/'manifest.json')[number-1]
        path = pack/entry['file']
        assert sha(path) == entry['sha256']
        cases.append(dict(case_id=f'{number:02}', path=str(path), sha256=sha(path),
            expected=entry['expected_source_id'], group='ood' if number == 12 else 'full' if number == 4 else 'partial'))
    files = [original, candidate, pack/'manifest.json'] + [Path(old['index_config'][k])
        for k in ['museum_corpus', 'museum_private_corpus', 'museum_search_fields']]
    folder.mkdir(parents=True, exist_ok=False)
    write_new(folder/'plan.json', dict(version=VERSION, cases=cases, repeats=REPEATS, max_calls=MAX_CALLS,
        model=cfg.deepseek_model, provider_host=urlparse(cfg.deepseek_base_url).hostname,
        index_config=old['index_config'], galleries=dict(original=str(original), back_reference=str(candidate)),
        visual_model=old['visual_model'], visual_cache=old['visual_cache'],
        files={str(p):sha(p) for p in files}, code_hashes={p:sha(ROOT/p) for p in CODE},
        scale_result_hash=sha(ROOT/'eval/private/neptune-scale-reference-v1/results.json'),
        scope='Fixed developer photos; BGE disabled in both arms; only gallery differs; legacy/single preserved',
        gates=['all four shared observations and 24 comparisons complete',
               'candidate reference assessments cover all supplied reference IDs',
               'candidate has no wrong identity including mixed candidates',
               'all in-corpus candidate results correct in all three repeats',
               'OOD candidate has no identity in all three repeats']))
    print(json.dumps(dict(prepared=len(cases), max_calls=MAX_CALLS, api_calls=0)))


def validate(folder):
    plan, cfg = read(folder/'plan.json'), MuseumSettings()
    assert plan['version'] == VERSION and plan['repeats'] == REPEATS and plan['max_calls'] == MAX_CALLS
    assert [c['case_id'] for c in plan['cases']] == [f'{n:02}' for n in CASES]
    assert cfg.deepseek_model == plan['model'] and urlparse(cfg.deepseek_base_url).hostname == plan['provider_host']
    for p, digest in plan['files'].items(): assert sha(Path(p)) == digest
    for p, digest in plan['code_hashes'].items(): assert sha(ROOT/p) == digest
    for c in plan['cases']: assert sha(Path(c['path'])) == c['sha256']
    return plan, cfg


def reserve(folder, details):
    number = len(list(folder.glob('request-*.json')))+1
    if number > MAX_CALLS: raise ValueError('Frozen call budget exhausted')
    write_new(folder/f'request-{number:02}.json', dict(number=number, reserved_at=time.time(), **details))
    return number


async def capture(folder):
    import torch
    torch.set_num_threads(4)
    plan, cfg = validate(folder)
    write_new(folder/'capture-started.json', dict(started_at=time.time()))
    frozen = MuseumSettings(_env_file=None, **plan['index_config'],
                            museum_photo_reference_mode='single', museum_photo_verification='legacy')
    store = MemoryStore(); index = MuseumIndex(frozen, store); rows = []
    try:
        await index.start()
        visuals = {}
        for mode in MODES:
            visuals[mode] = MuseumVisualIndex(Path(plan['galleries'][mode]), Path(plan['visual_model']),
                index.records, cache_dir=Path(plan['visual_cache']))
            await visuals[mode].start()
        for case in plan['cases']:
            observed = []; snapshots = []; observation_attempted = False
            real = MuseumEngine(cfg, store, index)._client()
            for mode in MODES:
                messages_seen = []
                class Capture:
                    async def complete_json(self, messages):
                        nonlocal observation_attempted
                        if messages[0]['content'].startswith('只描述照片'):
                            if observed: return observed[0]
                            if observation_attempted: raise ValueError('Observation failed; no retry')
                            observation_attempted = True
                            reserve(folder, dict(kind='observation', case_id=case['case_id']))
                            value = await real.complete_json(messages)
                            observed.append(value)
                            return value
                        messages_seen.append(messages)
                        return {'comparisons': []}  # capture only, never counted as a model result
                engine = MuseumEngine(frozen, store, index)
                engine.visual_index = visuals[mode]; engine.client_factory = Capture
                result = await PhotoRecognizer(engine).recognize(Path(case['path']).read_bytes(),
                    {'_id':'offline-'+VERSION+'-'+case['case_id']+'-'+mode})
                trace = await store.get('museum_photo_traces', result['trace_id'])
                saved = dict(case_id=case['case_id'], mode=mode, completed=False, trace=trace)
                if trace['error'] is None and len(messages_seen) == 1 and trace['observation_usable']:
                    scores = dict(zip(trace['visual_retrieved_ids'], trace['visual_scores'], strict=True))
                    refs = [dict(source_id=sid, reference_id=rid, **({'score':scores[sid]} if sid in scores else {}))
                        for sid, rid in zip(trace['comparison_reference_ids'], trace['comparison_image_ids'], strict=True)]
                    sources = [{k:index.records[sid][k] for k in ['_id','title','fields','source_url']}
                               for sid in trace['compared_ids']]
                    snapshot = dict(case_id=case['case_id'], expected=case['expected'], mode=mode,
                        messages=messages_seen[0], sources=sources, refs=refs, trace=trace,
                        visible_text=observed[0]['visible_text'], label_required_ids=sorted(visuals[mode].label_required_ids),
                        observation_hash=hashlib.sha256(json.dumps(observed[0],sort_keys=True).encode()).hexdigest())
                    name = case['case_id']+'-'+mode+'.json'; write_new(folder/name, snapshot)
                    saved.update(completed=True, snapshot=name, snapshot_hash=sha(folder/name),
                                 expected_in_refs=case['expected'] in [r['source_id'] for r in refs])
                    snapshots.append(snapshot)
                else: saved['error_type'] = trace['error'] or 'NoComparisonCaptured'
                rows.append(saved)
                print(json.dumps({k:saved.get(k) for k in ['case_id','mode','completed','expected_in_refs','error_type']}), flush=True)
            if len(snapshots) == 2:
                a, b = snapshots
                assert a['messages'][0] == b['messages'][0], 'Prompt must stay fixed'
                assert a['messages'][1]['content'][:2] == b['messages'][1]['content'][:2], 'Query photo drift'
                assert a['observation_hash'] == b['observation_hash']
                assert a['trace']['text_retrieved_ids'] == b['trace']['text_retrieved_ids']
            write_new(folder/('observation-'+case['case_id']+'.json'),
                dict(case_id=case['case_id'], observation=observed[0] if observed else None, usage_records=real.usage_records))
        write_new(folder/'capture.json', dict(complete=True, plan_hash=sha(folder/'plan.json'),
            corpus_hash=index.corpus_hash, corpus_count=len(index.records), rows=rows))
    finally:
        await index.close()


def assess(response, snapshot):
    applied = apply_response(response, snapshot, 'legacy')
    expected = {r['source_id'] for r in snapshot['refs']}
    returned = [r['candidate_id'] for r in response['comparisons']]
    applied['references_complete'] = expected.issubset(set(returned))
    return applied


def gate(rows, cases, capture_complete):
    wanted = {(c['case_id'],m,r) for c in cases for m in MODES for r in range(1,REPEATS+1)}
    keys = [(r['case_id'],r['mode'],r['repeat']) for r in rows]
    failures = []
    if not capture_complete: failures.append('capture_incomplete')
    if set(keys) != wanted or len(keys) != len(wanted): failures.append('comparison_matrix_incomplete')
    if any(not r.get('completed') for r in rows): failures.append('execution_failure')
    for c in cases:
        candidate = [r for r in rows if r['case_id']==c['case_id'] and r['mode']=='back_reference']
        good = 'correct_candidate' if c['expected'] else 'no_identity'
        if any(outcome(r,c['expected'])=='wrong_candidate' for r in candidate):
            failures.append(c['case_id']+':wrong_candidate')
        if any(not r.get('references_complete') for r in candidate):
            failures.append(c['case_id']+':missing_reference_assessment')
        if len(candidate)!=REPEATS or any(outcome(r,c['expected'])!=good for r in candidate):
            failures.append(c['case_id']+':task_not_preserved')
    return dict(passed=not failures, failures=failures, automatic_deployment=False)


async def live(folder):
    plan, cfg = validate(folder); capture = read(folder/'capture.json')
    assert capture['complete'] and capture['plan_hash'] == sha(folder/'plan.json')
    for entry in capture['rows']:
        if entry['completed']: assert sha(folder/entry['snapshot']) == entry['snapshot_hash']
    write_new(folder/'live-started.json', dict(started_at=time.time(), capture_hash=sha(folder/'capture.json')))
    engine = MuseumEngine(cfg, MemoryStore(), None); rows = []
    for repeat in range(1,REPEATS+1):
        for i, case in enumerate(plan['cases']):
            for mode in (MODES if (repeat+i)%2 else tuple(reversed(MODES))):
                entry = next(r for r in capture['rows'] if r['case_id']==case['case_id'] and r['mode']==mode)
                row = dict(case_id=case['case_id'], mode=mode, repeat=repeat, completed=False)
                if entry['completed']:
                    snapshot = read(folder/entry['snapshot']); client=engine._client(); started=time.perf_counter()
                    row['request_number'] = reserve(folder, dict(kind='comparison',case_id=case['case_id'],mode=mode,repeat=repeat))
                    try:
                        response = await client.complete_json(snapshot['messages']); row['response']=response
                        row.update(assess(response,snapshot),completed=True)
                    except Exception as exc:
                        row['error_type']=type(exc).__name__
                        row['error_cause']=type(exc.__cause__).__name__ if exc.__cause__ else None
                        if hasattr(exc,'errors'):
                            row['schema_issues']=[dict(field='.'.join(map(str,e['loc'])),type=e['type'])
                                for e in exc.errors(include_input=False,include_url=False)]
                    row.update(ms=round((time.perf_counter()-started)*1000),usage_records=client.usage_records)
                else: row['error_type']='CaptureFailed'
                row['outcome']=outcome(row,case['expected']); rows.append(row)
                write_new(folder/f'comparison-{len(rows):02}.json',row)
                print(json.dumps({k:row.get(k) for k in ['case_id','mode','repeat','outcome','candidate_ids','error_type','references_complete']}),flush=True)
    result=dict(complete=True,plan_hash=sha(folder/'plan.json'),capture_hash=sha(folder/'capture.json'),
        api_calls=len(list(folder.glob('request-*.json'))), rows=rows,
        gate=gate(rows,plan['cases'],all(r['completed'] for r in capture['rows'])))
    write_new(folder/'results.json',result)
    print(json.dumps(result['gate']),flush=True)


def replay(folder):
    plan, report, capture = (read(folder/n) for n in ['plan.json','results.json','capture.json'])
    assert report['plan_hash']==sha(folder/'plan.json') and report['capture_hash']==sha(folder/'capture.json')
    for entry in capture['rows']:
        if entry['completed']: assert sha(folder/entry['snapshot'])==entry['snapshot_hash']
    for row in report['rows']:
        if row['completed']:
            applied=assess(row['response'],read(folder/(row['case_id']+'-'+row['mode']+'.json')))
            for k,v in applied.items(): assert row[k]==v
    assert gate(report['rows'],plan['cases'],all(r['completed'] for r in capture['rows']))==report['gate']
    print(json.dumps(dict(replayed_rows=len(report['rows']),api_calls=0,gate=report['gate'])))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=['prepare','capture','live','replay'])
    parser.add_argument('--folder',type=output_folder,default=ROOT/'eval/private'/VERSION)
    args=parser.parse_args()
    if args.mode in {'capture','live'}: asyncio.run({'capture':capture,'live':live}[args.mode](args.folder))
    else: {'prepare':prepare,'replay':replay}[args.mode](args.folder)
