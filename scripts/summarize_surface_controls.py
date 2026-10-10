"""Publish only bounded metrics after reading back the private paired experiment.

This writes a local admin evaluation record and a sanitized JSON report. It never
publishes photos, OCR, raw provider output or a claim of production accuracy.
"""
from collections import Counter
import math
import time
import xml.etree.ElementTree as ET

import httpx
from pymongo import MongoClient

from evaluate_bottom_verification import read,sha,write_new
from evaluate_surface_controls import VERSION,CASES,MODES,outcome,gate
from app.museum.config import ROOT,MuseumSettings
from app.museum.eval_dashboard import EvalDisplay


def percentile(values,q):
    return sorted(values)[max(0,math.ceil(len(values)*q)-1)] if values else None


def summarize(folder):
    plan,capture,result=(read(folder/name) for name in ['plan.json','capture.json','results.json'])
    assert result['complete'] and result['plan_hash']==sha(folder/'plan.json')
    assert result['capture_hash']==sha(folder/'capture.json')
    assert [c['case_id'] for c in plan['cases']]==[f'{n:02}' for n in CASES]
    assert gate(result['rows'],plan['cases'],all(r['completed'] for r in capture['rows']))==result['gate']
    for row in capture['rows']:
        if row['completed']:assert sha(folder/row['snapshot'])==row['snapshot_hash']
    rows=[];groups=[];metrics=[];retrieval=[]
    for case in plan['cases']:
        entry=next(r for r in capture['rows'] if r['case_id']==case['case_id'])
        retrieval.append(dict(case_id=case['case_id'],group=case['group'],expected=case['expected'],
            capture_completed=entry['completed'],target_in_references=entry.get('expected_in_references') if case['expected'] else None))
        for mode in MODES:
            group=[r for r in result['rows'] if r['case_id']==case['case_id'] and r['mode']==mode]
            assert len(group)==3
            counts=Counter(outcome(r,case['expected']) for r in group)
            groups.append(dict(case_id=case['case_id'],group=case['group'],mode=mode,repeats=3,counts=dict(counts)))
            labels=[('正确候选','correct_candidate'),('错误身份候选','wrong_candidate'),
                    ('执行失败','execution_failure'),('仅相似浏览','similar_browse')]
            if case['group']=='ood':labels=[('正常无身份候选','no_identity'),('错误身份候选','wrong_candidate'),('执行失败','execution_failure')]
            for label,key in labels:
                metrics.append(dict(group=case['case_id'],variant=mode,label=label,unit='ratio',
                    value=counts[key]/3,numerator=counts[key],denominator=3))
            for row in group:
                assert row['outcome']==outcome(row,case['expected'])
                rows.append({k:row.get(k) for k in ['case_id','mode','repeat','outcome','completed','candidate_ids',
                    'number_ids','similar_ids','ms','error_type','schema_issues']})
    all_usage=[u for r in capture['rows']+result['rows'] for u in r.get('usage_records',[])]
    tokens={k:sum(u[k] for u in all_usage) if len(all_usage)==result['api_calls'] and
        all(isinstance(u.get(k),int) for u in all_usage) else None
        for k in ['prompt_tokens','completion_tokens','total_tokens']}
    known_tokens=sum(u.get('total_tokens',0) for u in all_usage if isinstance(u.get('total_tokens'),int))
    latency={m:dict(samples=len(v),p50_ms=percentile(v,.5),p95_ms=percentile(v,.95))
        for m in MODES for v in [[r['ms'] for r in result['rows'] if r['mode']==m and 'ms' in r]]}
    unsafe_surface=any(r['mode']=='surface' and r['outcome']=='wrong_candidate' for r in rows)
    decision='reject_surface_v1' if unsafe_surface else 'pending_controls' if result['gate']['passed'] else 'hold_default'
    summary=dict(unique_photos=6,repeats=3,comparison_runs=len(rows),api_calls=result['api_calls'],
        usage_records=len(all_usage),known_total_tokens=known_tokens,token_usage=tokens,cost=None,
        groups=groups,retrieval=retrieval,latency=latency,gate=result['gate'],default_enabled=False,
        identity_accuracy_measured=False,decision=decision,
        scope='Known developer photos; text reranker disabled; frozen OCR/candidates; not blind or online AB')
    return plan,capture,result,summary,rows,metrics


def main():
    folder=ROOT/'eval/private/surface-controls-v1'
    plan,capture,result,summary,rows,metrics=summarize(folder)
    suite=ET.parse(folder/'tests.xml').getroot().find('testsuite')
    assert suite.get('failures')==suite.get('errors')=='0'
    payload=dict(_id=VERSION+'-paired',created_at=time.time(),kind='photo',status='completed',
        dataset_version=VERSION,dataset_hash=sha(folder/'plan.json'),corpus_hash=capture['corpus_hash'],
        visual_index_hash=capture['index_hash'],model=plan['model'],prompt_version='legacy-v7-vs-surface-v1',
        human_reviewed=False,online_ab=False,summary=summary,results=rows,
        display=dict(track='photo',decision='reject' if summary['decision']=='reject_surface_v1' else 'pending',metrics=metrics,
            scope='六张已知开发图，旧新各三轮，共用每图一次真实观察；文字BGE关闭，同候选同参考图。失败不计拒识，海神背面漏召回单列。以预先冻结门槛决定是否继续试用；未启用默认。'))
    cfg=MuseumSettings()
    with httpx.Client(base_url='http://127.0.0.1:3000',trust_env=False,timeout=20,
        headers={'Authorization':'Bearer '+cfg.museum_admin_token}) as client:
        with MongoClient(cfg.mongodb_uri) as mongo:
            collection=mongo[cfg.mongodb_db].eval_runs
            if collection.find_one({'_id':payload['_id']}) is None:
                response=client.post('/api/museum/admin/eval-runs',json=payload);response.raise_for_status()
            saved=collection.find_one({'_id':payload['_id']})
            assert saved['results']==rows and saved['summary']==summary
        response=client.get('/api/museum/admin/evaluations');response.raise_for_status()
        shown=next(r for r in response.json()['runs'] if r['_id']==payload['_id'])
        assert shown['metrics']==EvalDisplay.model_validate(payload['display']).model_dump()['metrics']
        assert shown['decision']==payload['display']['decision']
        health=client.get('/api/museum/health');health.raise_for_status()
        assert health.json()['photo_verification']=='legacy'
    archive=dict(run_id=payload['_id'],mongo_readback=True,dashboard_readback=True,default_policy='legacy')
    public=dict(date='2026-10-10',corpus_count=capture['corpus_count'],**summary,rows=rows,archive=archive,
        backend_tests=int(suite.get('tests')),model=plan['model'],corpus_hash=capture['corpus_hash'],
        index_hash=capture['index_hash'],evidence_hashes={n:sha(folder/n) for n in ['plan.json','capture.json','results.json','tests.xml']})
    for p,v in [(folder/'archive.json',archive),(ROOT/'docs/surface-controls-results.json',public)]:
        if p.exists():assert read(p)==v
        else:write_new(p,v)
    import json
    print(json.dumps(dict(summary=summary,archive=archive),ensure_ascii=False))


if __name__=='__main__':main()
