"""Same frozen two photos/references, surface protocol only; at most six calls.

Private evidence is immutable. Preparation/replay do not call the provider.
This is a development protocol comparison, not an identification accuracy test.
"""
import argparse
import asyncio
import hashlib
import json
import time
from urllib.parse import urlparse

from evaluate_bottom_verification import read, sha, write_new, output_folder, IDS
from app.museum.config import ROOT, MuseumSettings
from app.museum.engine import MuseumEngine
from app.storage.store import MemoryStore
from app.museum.surface_verification import VERSION, surface_messages, parse_surface, decide_surface
from app.museum.photo_policy import add_number_clues

BASE = ROOT/'eval/private/bottom-verification-v1'
CODE = ['backend/app/museum/surface_verification.py','scripts/evaluate_surface_verification.py',
        'backend/app/museum/partial_verification.py','backend/app/museum/photo_policy.py']


def prepare(folder):
    plan, before = read(BASE/'plan.json'), read(BASE/'results.json')
    assert before['complete'] and before['plan_sha256']==sha(BASE/'plan.json')
    assert [r['case_id'] for r in plan['snapshots']]==IDS
    snapshots=[]
    for spec in plan['snapshots']:
        assert sha(BASE/spec['path'])==spec['sha256']
        snapshot=read(BASE/spec['path'])
        assert snapshot['expected'] in [h['source_id'] for h in snapshot['refs']]
        snapshots.append((spec,snapshot))
    folder.mkdir(parents=True,exist_ok=False)
    for spec,snapshot in snapshots:
        # Exact same user payload, no recropping or order/candidate changes.
        snapshot['messages']=surface_messages(snapshot['messages'])
        write_new(folder/spec['path'],snapshot)
    write_new(folder/'plan.json',dict(version=VERSION,model=plan['model'],provider_host=plan['provider_host'],
        max_calls=6,repeats=3,corpus_hash=plan['corpus_hash'],index_hash=plan['index_hash'],
        baseline_plan_hash=sha(BASE/'plan.json'),baseline_results_hash=sha(BASE/'results.json'),
        snapshots=[dict(case_id=s['case_id'],path=s['path'],sha256=sha(folder/s['path'])) for s,_ in snapshots],
        code_hashes={p:sha(ROOT/p) for p in CODE},human_reviewed=False,online_ab=False))
    print(json.dumps(dict(prepared=2,planned_calls=6,api_calls=0)))


def apply_surface(response,snapshot):
    rows=parse_surface(response,[h['source_id'] for h in snapshot['refs']])
    result,decisions,surfaces=decide_surface(rows,snapshot['sources'],snapshot['refs'],
        snapshot['visible_text'],set(snapshot['label_required_ids']))
    add_number_clues(result,snapshot['sources'],snapshot['trace']['ocr_exact_ids'])
    return dict(result=result,decisions=decisions,surface_summary=surfaces,
        expected_decision=next(r for r in decisions if r['candidate_id']==snapshot['expected']),
        candidate_ids=[r['id'] for r in result['candidates']],number_ids=[r['id'] for r in result['number_candidates']],
        similar_ids=[r['id'] for r in result['similar_candidates']])


async def live(folder):
    plan,cfg=read(folder/'plan.json'),MuseumSettings()
    assert plan['version']==VERSION and plan['max_calls']==6 and plan['repeats']==3
    assert [s['case_id'] for s in plan['snapshots']]==IDS
    assert cfg.deepseek_model==plan['model'] and urlparse(cfg.deepseek_base_url).hostname==plan['provider_host']
    assert sha(BASE/'results.json')==plan['baseline_results_hash'] and sha(BASE/'plan.json')==plan['baseline_plan_hash']
    for p,digest in plan['code_hashes'].items(): assert sha(ROOT/p)==digest
    for spec in plan['snapshots']:
        assert sha(folder/spec['path'])==spec['sha256']
        assert read(folder/spec['path'])['messages'][1]==read(BASE/spec['path'])['messages'][1]
    assert cfg.deepseek_api_key
    write_new(folder/'started.json',dict(started_at=time.time(),max_calls=6))
    engine= MuseumEngine(cfg,MemoryStore(),None)
    rows=[]
    for repeat in range(1,4):
        for spec in plan['snapshots']:
            snapshot=read(folder/spec['path']); n=len(rows)+1
            write_new(folder/f'call-{n}.json',dict(case_id=spec['case_id'],repeat=repeat,reserved_at=time.time()))
            row=dict(case_id=spec['case_id'],repeat=repeat,completed=False)
            client=engine._client(); started=time.perf_counter()
            try:
                response=await client.complete_json(snapshot['messages'])
                row['response']=response
                row.update(apply_surface(response,snapshot),completed=True)
            except Exception as exc:
                row['error_type']=type(exc).__name__
                if hasattr(exc,'errors'):
                    row['schema_issues']=[dict(field='.'.join(map(str,e['loc'])),type=e['type'])
                        for e in exc.errors(include_input=False,include_url=False)]
            row.update(ms=round((time.perf_counter()-started)*1000),usage_records=client.usage_records)
            write_new(folder/f'row-{n}.json',row);rows.append(row)
            print(json.dumps({k:row.get(k) for k in ['case_id','repeat','completed','expected_decision',
                'candidate_ids','number_ids','similar_ids','error_type']},ensure_ascii=False),flush=True)
    write_new(folder/'results.json',dict(complete=True,plan_hash=sha(folder/'plan.json'),api_calls=len(rows),rows=rows))


def replay(folder):
    plan,result=read(folder/'plan.json'),read(folder/'results.json')
    assert result['complete'] and result['plan_hash']==sha(folder/'plan.json')
    for spec in plan['snapshots']: assert sha(folder/spec['path'])==spec['sha256']
    for row in result['rows']:
        if row['completed']:
            snapshot=read(folder/(row['case_id']+'.json'))
            new=apply_surface(row['response'],snapshot)
            assert new['decisions']==row['decisions'] and new['surface_summary']==row['surface_summary']
    print(json.dumps(dict(replayed=len(result['rows']),api_calls=0)))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['prepare','live','replay'])
    parser.add_argument('--folder',type=output_folder,default=ROOT/'eval/private/surface-verification-v1')
    args=parser.parse_args()
    if args.command=='prepare':prepare(args.folder)
    elif args.command=='live':asyncio.run(live(args.folder))
    else:replay(args.folder)
