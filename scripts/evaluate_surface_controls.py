"""Frozen six-photo legacy/surface gate, three repeats, <=42 provider calls.

Six single observations feed the real photo retrieval/candidate builder. Text
reranking is disabled for this controlled comparison, as in the bottom study.
Both protocols see exactly the captured user payload. No retries or oracle IDs.
Images, OCR and full responses stay under eval/private; only metrics are public.
"""
import argparse
import asyncio
import json
import time
from pathlib import Path
from urllib.parse import urlparse

from evaluate_bottom_verification import read, sha, write_new, output_folder
from app.museum.config import ROOT, MuseumSettings
from app.museum.engine import MuseumEngine
from app.museum.retrieval import MuseumIndex
from app.museum.visual_index import MuseumVisualIndex
from app.museum.vision import PhotoRecognizer
from app.museum.photo_policy import Comparisons, decide, add_number_clues
from app.museum.surface_verification import surface_messages, parse_surface, decide_surface
from app.storage.store import MemoryStore

VERSION = 'surface-controls-v1'
CASES = (1, 4, 5, 8, 11, 12)
MODES = ('legacy', 'surface')
REPEATS = 3
MAX_CALLS = 42
CODE = ['scripts/evaluate_surface_controls.py','backend/app/museum/vision.py',
        'backend/app/museum/surface_verification.py','backend/app/museum/partial_verification.py',
        'backend/app/museum/photo_policy.py','backend/app/museum/retrieval.py',
        'backend/app/museum/visual_index.py','backend/app/museum/engine.py','backend/app/llm/client.py']


def prepare(folder):
    cfg=MuseumSettings()
    pack=ROOT/'data/private/MUSE-test-pack-12-20261006'
    entries=read(pack/'manifest.json')
    old=read(ROOT/'eval/private/bottom-mark-ocr-v1/plan.json')
    config=old['index_config']
    files={str(Path(p).resolve()):sha(Path(p)) for p in [
        config['museum_corpus'],config['museum_private_corpus'],config['museum_search_fields'],
        cfg.museum_visual_manifest,pack/'manifest.json']}
    cases=[]
    for n in CASES:
        row=entries[n-1];path=pack/row['file']
        assert sha(path)==row['sha256']
        cases.append(dict(case_id=f'{n:02}',expected=row['expected_source_id'],
            group='full' if n in (1,4) else 'partial' if n in (5,8) else 'ood',
            path=str(path),sha256=row['sha256'],source_page=row['source_page']))
    folder.mkdir(parents=True,exist_ok=False)
    write_new(folder/'plan.json',dict(version=VERSION,model=cfg.deepseek_model,
        provider_host=urlparse(cfg.deepseek_base_url).hostname,cases=cases,index_config=config,
        visual_manifest=str(cfg.museum_visual_manifest),visual_model=str(cfg.museum_visual_model),
        visual_cache=str(cfg.museum_visual_cache),files=files,code_hashes={p:sha(ROOT/p) for p in CODE},
        repeats=REPEATS,max_calls=MAX_CALLS,human_reviewed=False,online_ab=False,
        gates=['all observations/captures and 36 comparisons complete',
               'no wrong identity candidates, including mixed correct/wrong candidates',
               'surface full-object correct candidate 6/6',
               'no per-case correct-candidate count regression on in-corpus photos']))
    print(json.dumps(dict(prepared=6,planned_calls=MAX_CALLS,api_calls=0)))


def validate(folder):
    plan,cfg=read(folder/'plan.json'),MuseumSettings()
    assert plan['version']==VERSION and plan['max_calls']==MAX_CALLS and plan['repeats']==REPEATS
    assert [r['case_id'] for r in plan['cases']]==[f'{n:02}' for n in CASES]
    assert cfg.deepseek_model==plan['model'] and urlparse(cfg.deepseek_base_url).hostname==plan['provider_host']
    for p,digest in plan['code_hashes'].items(): assert sha(ROOT/p)==digest
    for p,digest in plan['files'].items(): assert sha(Path(p))==digest
    for row in plan['cases']: assert sha(Path(row['path']))==row['sha256']
    return plan,cfg


def reserve(folder,details):
    n=len(list(folder.glob('request-*.json')))+1
    if n>MAX_CALLS: raise ValueError('Frozen provider call cap exhausted')
    write_new(folder/f'request-{n:02}.json',dict(number=n,reserved_at=time.time(),**details))
    return n


async def capture(folder):
    import torch
    torch.set_num_threads(4)
    plan,cfg=validate(folder)
    write_new(folder/'capture-started.json',dict(started_at=time.time()))
    frozen=MuseumSettings(_env_file=None,**plan['index_config'],
        museum_photo_reference_mode='single',museum_photo_verification='legacy')
    store=MemoryStore();index=MuseumIndex(frozen,store);rows=[]
    try:
        await index.start()
        visual=MuseumVisualIndex(Path(plan['visual_manifest']),Path(plan['visual_model']),index.records,
            cache_dir=Path(plan['visual_cache']))
        await visual.start()
        for case in plan['cases']:
            engine=MuseumEngine(frozen,store,index);engine.visual_index=visual
            real=MuseumEngine(cfg,store,index)._client()
            observed=[];captured=[];observation_calls=0
            class Capture:
                async def complete_json(self,messages):
                    nonlocal observation_calls
                    if messages[0]['content'].startswith('只描述照片'):
                        if observation_calls: raise ValueError('No second observation allowed')
                        observation_calls+=1
                        reserve(folder,dict(kind='observation',case_id=case['case_id']))
                        response=await real.complete_json(messages)
                        observed.append(response)
                        return response
                    captured.append(messages)
                    return {'comparisons': []}  # capture only; never scored as model output
            engine.client_factory=Capture
            result=await PhotoRecognizer(engine).recognize(Path(case['path']).read_bytes(),
                {'_id':'offline-surface-control-'+case['case_id']})
            trace=await store.get('museum_photo_traces',result['trace_id'])
            row=dict(case_id=case['case_id'],completed=False,usage_records=real.usage_records,
                observation=observed[0] if observed else None,trace=trace)
            if trace['error'] is None and len(captured)==1 and trace['observation_usable']:
                scores=dict(zip(trace['visual_retrieved_ids'],trace['visual_scores'],strict=True))
                refs=[dict(source_id=sid,reference_id=rid,**({'score':scores[sid]} if sid in scores else {}))
                      for sid,rid in zip(trace['comparison_reference_ids'],trace['comparison_image_ids'],strict=True)]
                assert 0<len(refs)<=5 and len({r['source_id'] for r in refs})==len(refs)
                sources=[{k:index.records[sid][k] for k in ['_id','title','fields','source_url']}
                         for sid in trace['compared_ids']]
                snapshot=dict(case_id=case['case_id'],expected=case['expected'],group=case['group'],
                    messages=captured[0],sources=sources,refs=refs,visible_text=observed[0]['visible_text'],
                    label_required_ids=sorted(visual.label_required_ids),trace=trace)
                name=case['case_id']+'.json';write_new(folder/name,snapshot)
                row.update(completed=True,snapshot=name,snapshot_hash=sha(folder/name),
                    expected_in_references=case['expected'] in [r['source_id'] for r in refs])
            else:
                row['error_type']=trace['error'] or 'NoComparisonCaptured'
            write_new(folder/('observation-'+case['case_id']+'.json'),row);rows.append(row)
            print(json.dumps({k:row.get(k) for k in ['case_id','completed','expected_in_references','error_type']}),flush=True)
        write_new(folder/'capture.json',dict(complete=True,plan_hash=sha(folder/'plan.json'),
            corpus_hash=index.corpus_hash,index_hash=visual.index_hash,corpus_count=len(index.records),rows=rows))
    finally:
        await index.close()


def apply_response(response,snapshot,mode):
    if mode=='surface':
        parsed=parse_surface(response,[r['source_id'] for r in snapshot['refs']])
        result,decisions,surfaces=decide_surface(parsed,snapshot['sources'],snapshot['refs'],
            snapshot['visible_text'],set(snapshot['label_required_ids']))
    elif mode=='legacy':
        parsed=Comparisons.model_validate(response)
        result,decisions=decide(parsed,snapshot['sources'],snapshot['refs'],
            snapshot['visible_text'],set(snapshot['label_required_ids']))
        surfaces=[]
    else: raise ValueError('Unknown experiment arm')
    add_number_clues(result,snapshot['sources'],snapshot['trace']['ocr_exact_ids'])
    return dict(result=result,decisions=decisions,surface_summary=surfaces,
        candidate_ids=[r['id'] for r in result['candidates']],number_ids=[r['id'] for r in result['number_candidates']],
        similar_ids=[r['id'] for r in result['similar_candidates']])


def outcome(row,expected):
    if not row.get('completed'):return 'execution_failure'
    ids=row['candidate_ids']
    if any(sid!=expected for sid in ids):return 'wrong_candidate'
    if expected is None:return 'no_identity'
    if expected in ids:return 'correct_candidate'
    if expected in row['number_ids']:return 'number_browse'
    if expected in row['similar_ids']:return 'similar_browse'
    return 'no_identity'


def gate(rows,cases,capture_complete):
    expected={(c['case_id'],m,r) for c in cases for m in MODES for r in range(1,REPEATS+1)}
    actual=[(r['case_id'],r['mode'],r['repeat']) for r in rows]
    failures=[]
    if not capture_complete:failures.append('capture_incomplete')
    if set(actual)!=expected or len(actual)!=len(expected):failures.append('comparison_matrix_incomplete')
    if any(not r.get('completed') for r in rows):failures.append('execution_failure')
    lookup={c['case_id']:c for c in cases}
    if any(outcome(r,lookup[r['case_id']]['expected'])=='wrong_candidate' for r in rows):
        failures.append('wrong_candidate')
    for c in cases:
        counts={m:sum(outcome(r,c['expected'])=='correct_candidate' for r in rows
            if r['case_id']==c['case_id'] and r['mode']==m) for m in MODES}
        if c['group']=='full' and counts['surface']!=REPEATS:failures.append(c['case_id']+':full_not_preserved')
        if c['expected'] and counts['surface']<counts['legacy']:failures.append(c['case_id']+':candidate_regression')
    return dict(passed=not failures,failures=failures)


async def live(folder):
    plan,cfg=validate(folder);capture=read(folder/'capture.json')
    assert capture['complete'] and capture['plan_hash']==sha(folder/'plan.json')
    for item in capture['rows']:
        if item['completed']:assert sha(folder/item['snapshot'])==item['snapshot_hash']
    write_new(folder/'live-started.json',dict(started_at=time.time(),capture_hash=sha(folder/'capture.json')))
    engine=MuseumEngine(cfg,MemoryStore(),None);rows=[]
    for repeat in range(1,REPEATS+1):
        for i,case in enumerate(plan['cases']):
            captured=next(r for r in capture['rows'] if r['case_id']==case['case_id'])
            modes=MODES if (repeat+i)%2 else tuple(reversed(MODES))
            for mode in modes:
                row=dict(case_id=case['case_id'],mode=mode,repeat=repeat,completed=False)
                if captured['completed']:
                    snapshot=read(folder/captured['snapshot'])
                    messages=surface_messages(snapshot['messages']) if mode=='surface' else snapshot['messages']
                    assert messages[1]==snapshot['messages'][1]
                    client=engine._client();started=time.perf_counter()
                    row['request_number']=reserve(folder,dict(kind='comparison',case_id=case['case_id'],mode=mode,repeat=repeat))
                    try:
                        response=await client.complete_json(messages);row['response']=response
                        row.update(apply_response(response,snapshot,mode),completed=True)
                    except Exception as exc:
                        row['error_type']=type(exc).__name__
                        if hasattr(exc,'errors'):
                            row['schema_issues']=[dict(field='.'.join(map(str,e['loc'])),type=e['type'])
                                for e in exc.errors(include_input=False,include_url=False)]
                    row.update(ms=round((time.perf_counter()-started)*1000),usage_records=client.usage_records)
                else:row['error_type']='CaptureFailed'
                row['outcome']=outcome(row,case['expected']);rows.append(row)
                write_new(folder/f'comparison-{len(rows):02}.json',row)
                print(json.dumps({k:row.get(k) for k in ['case_id','mode','repeat','outcome','candidate_ids','error_type']}),flush=True)
    verdict=gate(rows,plan['cases'],all(r['completed'] for r in capture['rows']))
    write_new(folder/'results.json',dict(complete=True,plan_hash=sha(folder/'plan.json'),capture_hash=sha(folder/'capture.json'),
        api_calls=len(list(folder.glob('request-*.json'))),gate=verdict,rows=rows))
    print(json.dumps(verdict),flush=True)


def replay(folder):
    plan,result=read(folder/'plan.json'),read(folder/'results.json')
    capture=read(folder/'capture.json')
    assert result['plan_hash']==sha(folder/'plan.json') and result['capture_hash']==sha(folder/'capture.json')
    for item in capture['rows']:
        if item['completed']:assert sha(folder/item['snapshot'])==item['snapshot_hash']
    for row in result['rows']:
        if row['completed']:
            again=apply_response(row['response'],read(folder/(row['case_id']+'.json')),row['mode'])
            for key,value in again.items():assert row[key]==value
    assert gate(result['rows'],plan['cases'],all(r['completed'] for r in capture['rows']))==result['gate']
    print(json.dumps(dict(replayed=len(result['rows']),api_calls=0,gate=result['gate'])))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['prepare','capture','live','replay'])
    parser.add_argument('--folder',type=output_folder,default=ROOT/'eval/private/surface-controls-v1')
    args=parser.parse_args()
    if args.command=='prepare':prepare(args.folder)
    elif args.command=='capture':asyncio.run(capture(args.folder))
    elif args.command=='live':asyncio.run(live(args.folder))
    else:replay(args.folder)
