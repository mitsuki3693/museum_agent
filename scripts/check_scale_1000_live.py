"""Live local acceptance. Seven fresh sessions; at most 30 paid text calls.

All generated questions concern public catalogue material. No photo upload.
Credentials are read through settings and never saved in the private report.
--verify-restart is read-only and checks the same Trace/feedback documents.
"""
import argparse
import hashlib
import json
from pathlib import Path
import time
from uuid import uuid4

from bson import json_util
import httpx
from pymongo import MongoClient
from app.museum.config import MuseumSettings

ROOT=Path(__file__).resolve().parents[1]


def digest(value):
    return hashlib.sha256(json_util.dumps(value,sort_keys=True,
        json_options=json_util.CANONICAL_JSON_OPTIONS).encode()).hexdigest()


def main(output, verify_restart=False):
    cfg=MuseumSettings()
    if not output.resolve().is_relative_to((ROOT/'eval/private').resolve()):
        raise ValueError('Use a private report path')
    with httpx.Client(base_url='http://127.0.0.1:3000',timeout=120,trust_env=False) as client, MongoClient(cfg.mongodb_uri) as mongo:
        health=client.get('/api/museum/health');health.raise_for_status();health=health.json()
        assert health['corpus_count']==1012 and health['storage']=='mongo'
        db=mongo[cfg.mongodb_db]
        if verify_restart:
            before=json.loads(output.read_bytes())
            assert before['health']['corpus_hash']==health['corpus_hash']
            checks=[dict(collection=c, id=sid, matched=digest(db[c].find_one({'_id':sid}))==wanted)
                    for c, entries in before['persisted_hashes'].items() for sid,wanted in entries.items()]
            assert all(c['matched'] for c in checks)
            with output.with_name(output.stem+'-restart.json').open('x',encoding='utf-8') as f:
                json.dump(dict(complete=True,health=health,checks=checks),f,indent=2)
            print('Restart persistence checks passed:',len(checks));return
        if output.exists():raise FileExistsError('Preserve evidence')
        report=dict(complete=False,scope='developer local smoke, not user accuracy or concurrency test',
                    health=health,checks=[],traces=[],persisted_hashes={})
        with output.open('x',encoding='utf-8') as stream:
            def save():
                stream.seek(0);json.dump(report,stream,ensure_ascii=False,indent=2);stream.truncate();stream.flush()
            objects=client.get('/api/museum/objects');objects.raise_for_status();items=objects.json()
            assert len(items)==1012 and len({r['id'] for r in items})==1012
            report['checks'].append(dict(kind='catalogue_count',count=len(items)))
            for sid in ['va-o1803107','va-o1388779','va-o491703','va-o498381','va-o1302988']:
                item=next(r for r in items if r['id']==sid)
                image=client.get(item['image_url']);image.raise_for_status()
                assert image.headers['content-type'].startswith('image/') and len(image.content)>100
                report['checks'].append(dict(kind='new_image',id=sid,bytes=len(image.content)))
            for endpoint in ['routes/options','routes/floor-demo','demo/operations']:
                response=client.get('/api/museum/'+endpoint);response.raise_for_status()
                report['checks'].append(dict(kind='demo_endpoint',endpoint=endpoint,status=response.status_code))
            records=json.loads((ROOT/'data/private/va-pilot-1000-v1-corpus.json').read_bytes())
            number=next(r['fields']['accession_number'] for r in records if r['_id']=='va-o1803107')
            cases=[dict(name='old_accession',query='C.2360-1910',expected='va-o162156'),
                   dict(name='new_accession',query=number,expected='va-o1803107'),
                   dict(name='new_glass_description',query='找彭毅 Roles 系列 No.5 的玻璃王冠雕塑',gold='va-o1803107'),
                   dict(name='new_bust_description',query='找低着头的黑色石膏萨福半身像',gold='va-o1388779'),
                   dict(name='new_chair_description',query='找红漆椅子，椅背中央有蝙蝠和云纹',gold='va-o491703'),
                   dict(name='new_brief',query='请简短介绍这件作品。',object_id='va-o1803107'),
                   dict(name='new_unknown',query='制作者当天早餐吃了什么？',object_id='va-o1803107')]
            trace_ids=[];feedback_ids=[]
            for c in cases:
                created=client.post('/api/museum/sessions');created.raise_for_status()
                session=created.json();headers={'Authorization':'Bearer '+session['token']}
                payload=dict(query=c['query'],request_id=str(uuid4()))
                if c.get('object_id'):payload['object_id']=c['object_id']
                start=time.perf_counter();response=client.post('/api/museum/chat',json=payload,headers=headers)
                item=dict(name=c['name'],http_status=response.status_code,ms=round((time.perf_counter()-start)*1000))
                # Preserve provider/service errors, do not retry for a better score.
                if response.status_code==200:
                    answer=response.json();tid=answer['trace_id'];trace_ids.append(tid)
                    item.update(trace_id=tid,status=answer['status'],answer=answer.get('answer'),retrieved_ids=answer.get('retrieved_ids'))
                    if c.get('gold'):
                        item.update(development_gold=c['gold'],gold_retrieved=c['gold'] in answer.get('retrieved_ids',[]),
                            gold_presented=c['gold'] in [v['id'] for v in answer.get('candidates',[])])
                    if 'expected' in c:
                        assert c['expected'] in answer['retrieved_ids']
                        assert answer['status']=='needs_confirmation'
                        repeated=client.post('/api/museum/chat',json=payload,headers=headers);repeated.raise_for_status()
                        assert repeated.json()['trace_id']==tid
                        assert client.post('/api/museum/chat',json={**payload,'query':'different request'},headers=headers).status_code==409
                        item['idempotency_and_conflict']='passed'
                    # Only mark a deterministic lookup helpful; this is a
                    # development feedback fixture, not a visitor evaluation.
                    if c['name']=='new_accession':
                        feedback=client.post('/api/museum/feedback',headers=headers,json=dict(trace_id=tid,kind='helpful',
                            comment='开发验收：新增编号候选正确；不计真实游客满意度。'))
                        feedback.raise_for_status()
                        feedback_ids=[r['_id'] for r in db.museum_feedback.find({'trace_id':tid})]
                else:
                    item['error_body']=response.text[:200]
                    sid=hashlib.sha256(session['token'].encode()).hexdigest()
                    failed=db.museum_traces.find_one({'session_id':sid},sort=[('created_at',-1)])
                    if failed:
                        trace_ids.append(failed['_id']);item['trace_id']=failed['_id']
                report['traces'].append(item);save();print(json.dumps(item,ensure_ascii=False),flush=True)
            for collection,ids in [('museum_traces',trace_ids),('museum_feedback',feedback_ids)]:
                report['persisted_hashes'][collection]={sid:digest(db[collection].find_one({'_id':sid})) for sid in ids}
            evidence=json.loads((ROOT/'eval/private/scale-1000-candidate-v1.json').read_bytes())
            from evaluate_scale_1000 import percentiles
            evidence['summary']['normal_latency_ms']=percentiles([r['ms'] for r in evidence['normal']])
            evidence['summary']['latency_percentile_method']='nearest-rank; 12 service samples are not an SLA'
            # Store all 114 regression cases in two batches to respect the API's
            # 80-row limit; the summary is shared and explicitly offline.
            rows=[dict(case_id=r['id'],variant='fallback-1000',rank=r['rank'],latency_ms=r['ms'])
                  for r in evidence['text'] if r['gold'] and r['kind']!='extra']
            report['eval_runs']=[]
            for offset in range(0,len(rows),80):
                run=dict(_id='scale1000-'+uuid4().hex,created_at=time.time(),kind='text',status='completed',
                    dataset_version='scale1000-development-frozen-old-queries-v1',dataset_hash=evidence['frozen_questions_sha256'],
                    corpus_hash=evidence['corpus_hash'],model='no-generation',prompt_version='retrieval-only',
                    embedding_model=evidence['embedding_model'],human_reviewed=False,online_ab=False,
                    summary={**evidence['summary'],'scope':'offline development retrieval; not answer accuracy',
                             'part':offset//80+1,'total_cases':len(rows)},results=rows[offset:offset+80])
                reply=client.post('/api/museum/admin/eval-runs',headers={'Authorization':'Bearer '+cfg.museum_admin_token},json=run)
                reply.raise_for_status();report['eval_runs'].append(run['_id'])
            report['persisted_hashes']['eval_runs']={sid:digest(db.eval_runs.find_one({'_id':sid})) for sid in report['eval_runs']}
            report['complete']=True;save()


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--verify-restart',action='store_true');args=p.parse_args();main(args.output,args.verify_restart)
