"""Replay the 13 exact-field contracts through the web proxy and MongoDB.

Uses one conversation to exercise history isolation. No model call is expected;
any usage or rewrite is a failing result. Does not retry failed requests.
"""
import argparse
import json
from pathlib import Path
import time
from uuid import uuid4

import httpx
from pymongo import MongoClient

from app.museum.config import MuseumSettings, ROOT


def main(output):
    if not output.resolve().is_relative_to((ROOT/'eval/private').resolve()):
        raise ValueError('Use a private output path')
    cfg=MuseumSettings()
    assert cfg.museum_metadata_routing
    offline=json.loads((ROOT/'eval/private/catalogue-metadata-v1.json').read_bytes())
    assert offline['complete'] and offline['contract_passed']==13
    report=dict(complete=False,scope='Local exact-field developer acceptance, not general retrieval accuracy',rows=[])
    with output.open('x',encoding='utf-8') as f, httpx.Client(base_url='http://127.0.0.1:3000',
        timeout=30,trust_env=False) as client, MongoClient(cfg.mongodb_uri) as mongo:
        db=mongo[cfg.mongodb_db]
        health=client.get('/api/museum/health');health.raise_for_status();report['health']=health.json()
        assert report['health']['corpus_count']==1012 and report['health']['storage']=='mongo'
        created=client.post('/api/museum/sessions');created.raise_for_status()
        headers={'Authorization':'Bearer '+created.json()['token']}
        for case in offline['contracts']:
            payload=dict(query=case['query'],request_id=str(uuid4()))
            start=time.perf_counter();response=client.post('/api/museum/chat',headers=headers,json=payload)
            row=dict(id=case['id'],http_status=response.status_code,ms=round((time.perf_counter()-start)*1000))
            if response.status_code==200:
                result=response.json();trace=db.museum_traces.find_one({'_id':result['trace_id']})
                assert trace is not None
                ids=[c['id'] for c in result.get('candidates',[])]
                row.update(result=result,metadata=trace['rerank'].get('metadata'),
                    retrieval_route=trace['retrieval_route'],rewritten_query=trace['rewritten_query'],
                    attempts=trace['attempts'],expected_count=len(case['expected']))
                row['passed']=(set(ids)<=set(case['expected']) and len(ids)==min(5,len(case['expected']))
                    and trace['retrieval_route']=='catalogue_metadata' and trace['rewritten_query']==case['query']
                    and not result['usage'] and not trace['attempts'] and not trace['rewrite_error']
                    and result['status']==('needs_confirmation' if case['expected'] else 'insufficient_evidence'))
                if case['id']=='bologna-en':
                    repeat=client.post('/api/museum/chat',headers=headers,json=payload);repeat.raise_for_status()
                    conflict=client.post('/api/museum/chat',headers=headers,json={**payload,'query':'Find works dated 2029'})
                    row['idempotency_passed']=repeat.json()['trace_id']==result['trace_id'] and conflict.status_code==409
                    row['passed']=row['passed'] and row['idempotency_passed']
            else:
                row.update(passed=False,service_error=response.text[:150])
            report['rows'].append(row)
            f.seek(0);json.dump(report,f,ensure_ascii=False,indent=2);f.truncate();f.flush()
            print(json.dumps({k:row[k] for k in ['id','http_status','ms','passed']},ensure_ascii=False),flush=True)
        report['complete']=True;report['passed']=sum(r['passed'] for r in report['rows'])
        f.seek(0);json.dump(report,f,ensure_ascii=False,indent=2);f.truncate()
        assert report['passed']==13


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    main(parser.parse_args().output)
