"""Publish bounded comparison evidence, never image-bearing snapshots."""
from collections import Counter
import time
import xml.etree.ElementTree as ET

import httpx
from pymongo import MongoClient

from evaluate_bottom_verification import ROOT, read, sha, write_new
from evaluate_neptune_identity import VERSION, MODES, gate, outcome
from summarize_surface_controls import percentile
from app.museum.config import MuseumSettings
from app.museum.eval_dashboard import EvalDisplay, ratio


def summarize(folder):
    plan, capture, result = (read(folder/n) for n in ['plan.json','capture.json','results.json'])
    assert result['complete'] and result['plan_hash']==sha(folder/'plan.json')
    assert result['capture_hash']==sha(folder/'capture.json')
    assert gate(result['rows'],plan['cases'],all(r['completed'] for r in capture['rows']))==result['gate']
    observations=[read(folder/('observation-'+c['case_id']+'.json')) for c in plan['cases']]
    groups=[]; metrics=[]; rows=[]; retrieval=[]
    for entry in capture['rows']:
        if entry['completed']:assert sha(folder/entry['snapshot'])==entry['snapshot_hash']
        trace=entry['trace']
        retrieval.append(dict(case_id=entry['case_id'],mode=entry['mode'],completed=entry['completed'],
            expected_in_refs=entry.get('expected_in_refs'),visual_ids=trace['visual_retrieved_ids'],
            reference_ids=trace['comparison_reference_ids'],image_ids=trace['comparison_image_ids']))
    for case in plan['cases']:
        for mode in MODES:
            values=[r for r in result['rows'] if r['case_id']==case['case_id'] and r['mode']==mode]
            assert len(values)==3
            counts=Counter(outcome(r,case['expected']) for r in values)
            groups.append(dict(case_id=case['case_id'],mode=mode,counts=dict(counts),repeats=3))
            for label,key in [('正确候选','correct_candidate'),('错误身份候选','wrong_candidate'),
                              ('执行失败','execution_failure'),('无身份候选','no_identity')]:
                metrics.append(ratio(case['case_id'],mode,label,counts[key],3))
            for row in values:
                assert row['outcome']==outcome(row,case['expected'])
                rows.append({k:row.get(k) for k in ['case_id','mode','repeat','outcome','completed',
                    'candidate_ids','similar_ids','number_ids','references_complete','ms','error_type','error_cause','schema_issues']})
    usage=[u for r in observations+result['rows'] for u in r.get('usage_records',[])]
    known=sum(u['total_tokens'] for u in usage if isinstance(u.get('total_tokens'),int))
    known_complete=len(usage)==result['api_calls'] and all(isinstance(u.get('total_tokens'),int) for u in usage)
    latency={mode:dict(samples=len(values),p50_ms=percentile(values,.5),p95_ms=percentile(values,.95))
             for mode in MODES for values in [[r['ms'] for r in result['rows'] if r['mode']==mode and 'ms' in r]]}
    summary=dict(unique_development_photos=4,comparison_runs=len(rows),groups=groups,retrieval=retrieval,
        api_calls=result['api_calls'],usage_records=len(usage),known_total_tokens=known,
        total_tokens=known if known_complete else None,cost=None,latency=latency,gate=result['gate'],
        default_enabled=False,decision='pending_trial' if result['gate']['passed'] else 'hold_original',
        scope=plan['scope'],human_reviewed=False,online_ab=False)
    return plan,capture,summary,rows,metrics


def main():
    folder=ROOT/'eval/private'/VERSION
    plan,capture,summary,rows,metrics=summarize(folder)
    suite=ET.parse(folder/'tests.xml').getroot().find('testsuite')
    assert suite.get('failures')==suite.get('errors')=='0'
    payload=dict(_id=VERSION,created_at=time.time(),kind='photo',status='completed',
        dataset_version=VERSION,dataset_hash=sha(folder/'plan.json'),corpus_hash=capture['corpus_hash'],
        model=plan['model'],prompt_version='legacy-v7-gallery-only-ablation',human_reviewed=False,online_ab=False,
        summary=summary,results=rows,display=dict(track='photo',decision='pending' if summary['gate']['passed'] else 'reject',
            metrics=metrics,scope='四张已知开发图，原图库与补背面图库各三轮，同图共用一次观察。BGE关闭，核对协议不变，仅参考图库改变。失败不计拒识，尚未自动切换网站。'))
    cfg=MuseumSettings()
    with httpx.Client(base_url='http://127.0.0.1:3000',trust_env=False,timeout=20,
                      headers={'Authorization':'Bearer '+cfg.museum_admin_token}) as client:
        with MongoClient(cfg.mongodb_uri) as mongo:
            collection=mongo[cfg.mongodb_db].eval_runs
            if collection.find_one({'_id':VERSION}) is None:
                response=client.post('/api/museum/admin/eval-runs',json=payload);response.raise_for_status()
            saved=collection.find_one({'_id':VERSION})
            assert saved['summary']==summary and saved['results']==rows
        response=client.get('/api/museum/admin/evaluations');response.raise_for_status()
        shown=next(r for r in response.json()['runs'] if r['_id']==VERSION)
        assert shown['decision']==payload['display']['decision']
        assert shown['metrics']==EvalDisplay.model_validate(payload['display']).model_dump()['metrics']
        response=client.get('/api/museum/health');response.raise_for_status()
        assert response.json()['photo_verification']=='legacy'
    archive=dict(run_id=VERSION,mongo_readback=True,dashboard_readback=True,default_policy='legacy')
    public=dict(date='2026-10-10',corpus_count=capture['corpus_count'],**summary,rows=rows,archive=archive,
        relevant_tests=int(suite.get('tests')),model=plan['model'],corpus_hash=capture['corpus_hash'],
        evidence_hashes={n:sha(folder/n) for n in ['plan.json','capture.json','results.json','tests.xml']})
    for path,value in [(folder/'archive.json',archive),(ROOT/'docs/neptune-identity-results.json',public)]:
        if path.exists():assert read(path)==value
        else:write_new(path,value)
    import json
    print(json.dumps(dict(groups=summary['groups'],gate=summary['gate'],api_calls=summary['api_calls'],archive=archive)))


if __name__=='__main__':main()
