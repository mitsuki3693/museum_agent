"""Real MongoDB + separate API process reliability acceptance, with a fake model.

Creates only museum_test_* / museum_restore_* databases on a dedicated local instance.
Keeps database files and reports for inspection. Does not touch the active museum database.
"""
import argparse
import asyncio
import hashlib
import io
import json
import os
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from PIL import Image
from pymongo import MongoClient
from bson import json_util
from app.museum.runtime import RUNTIME_COLLECTIONS

ROOT=Path(__file__).resolve().parents[2]

def check(args):
    stamp=uuid.uuid4().hex[:10];work=ROOT/'.runtime'/('mongo-acceptance-'+stamp);work.mkdir(parents=True)
    dbname='museum_test_'+stamp;uri='mongodb://127.0.0.1:27018'
    database=work/'data';database.mkdir();report={'kind':'real MongoDB reliability; deterministic fake LLM', 'run_id':stamp,'checks':{},'real_model_calls':0}
    corpus=work/'corpus.json';corpus.write_text(json.dumps([{'_id':'vase','title':'Test Vase','content':'Material: bronze. Date: 1884.',
        'source_url':'https://example.org/fixture','license':'CC0','source_hash':'fixture','fetched_at':'2026-10-01','status':'active'}]))
    config=work/'config.json';config.write_text(json.dumps({'museum_storage':'mongo','mongodb_uri':uri,'mongodb_db':dbname,
        'museum_corpus':str(corpus),'museum_embedding':'lexical','museum_admin_token':'acceptance-only',
        'deepseek_api_key':'fake-never-sent','museum_max_inflight':24,'museum_session_ttl':3600,
        'museum_mongodump':str(Path(args.dump).resolve()),'museum_backup_dir':str(work/'backups')}))
    env={**os.environ,'PYTHONPATH':str(ROOT/'backend'),'MUSEUM_ACCEPTANCE_CONFIG':str(config)}
    flags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
    mongo=api=None;logs=[];client=MongoClient(uri,serverSelectionTimeoutMS=500,tz_aware=True)
    def launch(command,name):
        log=(work/name).open('ab');logs.append(log)
        return subprocess.Popen(command,cwd=ROOT,env=env,stdout=log,stderr=log,creationflags=flags)
    def start_mongo():
        proc=launch([args.mongod,'--dbpath',str(database),'--bind_ip','127.0.0.1','--port','27018','--setParameter','ttlMonitorSleepSecs=1'],'mongo.log')
        for _ in range(80):
            try:client.admin.command('ping');return proc
            except Exception:time.sleep(.25)
        stop(proc)
        raise RuntimeError('Mongo test instance unavailable')
    def start_api():
        proc=launch([sys.executable,'-m','uvicorn','scripts.museum_acceptance_server:make_app','--factory','--host','127.0.0.1','--port','18000'],'api.log')
        for _ in range(80):
            try:
                if httpx.get('http://127.0.0.1:18000/api/museum/health',timeout=1).status_code==200:return proc
            except Exception:pass
            time.sleep(.25)
        stop(proc)
        raise RuntimeError('Acceptance API unavailable')
    def stop(proc):
        if proc and proc.poll() is None:proc.terminate();proc.wait(timeout=20)
    def snapshot(db):
        result={}
        for name in RUNTIME_COLLECTIONS:
            rows=list(db[name].find().sort('_id',1))
            result[name]={'count':len(rows),'sha256':hashlib.sha256(json_util.dumps(rows,json_options=json_util.CANONICAL_JSON_OPTIONS,sort_keys=True).encode()).hexdigest()}
        return result
    def mark(name,value):report['checks'][name]=value;print(name, 'PASS',flush=True)
    try:
        # Never attach to a pre-existing test instance.
        try:client.admin.command('ping')
        except Exception:pass
        else:raise RuntimeError('Dedicated acceptance port 27018 is already occupied')
        mongo=start_mongo();api=start_api();db=client[dbname];report['mongodb_version']=client.server_info()['version']
        c=httpx.Client(base_url='http://127.0.0.1:18000',timeout=30);admin={'Authorization':'Bearer acceptance-only'}
        def session():
            r=c.post('/api/museum/sessions');r.raise_for_status();return {'Authorization':'Bearer '+r.json()['token']}
        a,b=session(),session();bodies=[];trace_ids=[]
        for i in range(10):
            body={'query':f'question {i}: material','object_id':'vase','request_id':str(uuid.uuid4())};bodies.append(body)
            r=c.post('/api/museum/chat',headers=a,json=body);r.raise_for_status();trace_ids.append(r.json()['trace_id'])
        img=io.BytesIO();Image.new('RGB',(32,32),'blue').save(img,format='JPEG')
        for _ in range(3):assert c.post('/api/museum/recognize',headers=a,files={'photo':('fixture.jpg',img.getvalue(),'image/jpeg')}).json()['status']=='needs_confirmation'
        for tid in trace_ids[:3]:assert c.post('/api/museum/feedback',headers=a,json={'trace_id':tid,'kind':'helpful'}).status_code==200
        run={'_id':'reliability-fixture','created_at':time.time(),'kind':'reliability','status':'completed',
             'dataset_version':'fake-fixture','dataset_hash':'a'*64,'corpus_hash':'b'*64,'model':'fixture',
             'prompt_version':'fixture','summary':{'purpose':'storage check, not model accuracy'},'results':[]}
        assert c.post('/api/museum/admin/eval-runs',headers=admin,json=run).status_code==200
        assert c.post('/api/museum/admin/eval-runs',headers=admin,json=run).status_code==409
        before=snapshot(db);stop(api);api=None;stop(mongo);mongo=None
        mongo=start_mongo();api=start_api();assert snapshot(db)==before
        assert before['museum_traces']['count']==10 and before['museum_photo_traces']['count']==3 and before['museum_feedback']['count']==3
        mark('full_process_restart',before)
        for _ in range(2):assert c.post('/api/museum/chat',headers=a,json=bodies[0]).json()['trace_id']==trace_ids[0]
        assert db.museum_traces.count_documents({})==10
        mark('idempotency',{'same_result':True,'trace_count':10})
        assert c.post('/api/museum/chat',headers=a,json={**bodies[0],'query':'different'}).status_code==409
        mark('fingerprint_conflict',{'http_status':409})
        assert c.get('/api/museum/traces/'+trace_ids[0],headers=b).status_code==404
        assert c.post('/api/museum/feedback',headers=b,json={'trace_id':trace_ids[0],'kind':'wrong_fact'}).status_code==404
        assert c.get('/api/museum/admin/traces',headers=b).status_code==403
        mark('session_isolation',{'cross_session_read':404,'cross_session_feedback':404,'visitor_admin':403})
        b_sid=hashlib.sha256(b['Authorization'][7:].encode()).hexdigest()
        db.museum_sessions.update_one({'_id':b_sid},{'$set':{'expires_at':time.time()-.01,'purge_at':datetime.now(timezone.utc)}})
        assert c.post('/api/museum/chat',headers=b,json=bodies[0]).status_code==401
        deadline=time.monotonic()+8
        while db.museum_sessions.find_one({'_id':b_sid}) and time.monotonic()<deadline:time.sleep(.1)
        assert db.museum_sessions.find_one({'_id':b_sid}) is None
        indexes=list(db.museum_sessions.list_indexes());assert any(i.get('expireAfterSeconds')==0 for i in indexes)
        mark('ttl',{'request_rejected_immediately':True,'mongo_removed_document':True,'test_monitor_seconds':1,'production_monitor_default':True})
        headers=[session() for _ in range(20)];ids_by_session={};latencies=[]
        def worker(pair):
            n,h=pair;ids=[]
            with httpx.Client(base_url='http://127.0.0.1:18000',timeout=30) as worker_client:
                for j in range(10):
                    body={'query':f'load {n}-{j}: material','object_id':'vase','request_id':str(uuid.uuid4())}
                    started=time.perf_counter();r=worker_client.post('/api/museum/chat',headers=h,json=body);r.raise_for_status();d=r.json();latencies.append((time.perf_counter()-started)*1000);ids.append(d['trace_id'])
                    again=worker_client.post('/api/museum/chat',headers=h,json=body);again.raise_for_status();assert again.json()['trace_id']==d['trace_id']
            return hashlib.sha256(h['Authorization'][7:].encode()).hexdigest(),ids
        with ThreadPoolExecutor(max_workers=20) as pool:
            for sid,ids in pool.map(worker,enumerate(headers)):ids_by_session[sid]=ids
        assert db.museum_traces.count_documents({})==210 and db.museum_request_cache.count_documents({})==210
        for sid,ids in ids_by_session.items():assert {r['_id'] for r in db.museum_traces.find({'session_id':sid})}==set(ids)
        assert len({i for ids in ids_by_session.values() for i in ids})==200
        mark('concurrency',{'sessions':20,'unique_requests':200,'duplicate_replays':200,'persisted_unique_traces':200,'lost':0,'cross_session':0})
        from app.museum.api import ChatRequest
        interrupted={'query':'interruption fixture','request_id':str(uuid.uuid4())}
        sid=hashlib.sha256(a['Authorization'][7:].encode()).hexdigest()
        db.museum_request_cache.insert_one({'_id':sid+':'+interrupted['request_id'],'session_id':sid,
            'request_id':interrupted['request_id'],'trace_id':'interrupted-fixture','state':'pending',
            'query':interrupted['query'],'fingerprint':hashlib.sha256(ChatRequest(**interrupted).model_dump_json(exclude={'request_id'}).encode()).hexdigest(),
            'created_at':time.time(),'expires_at':time.time()+1800,'purge_at':datetime.now(timezone.utc)+timedelta(minutes=30)})
        stop(api);api=start_api()
        assert db.museum_traces.find_one({'_id':'interrupted-fixture'})['status']=='interrupted'
        assert c.post('/api/museum/chat',headers=a,json=interrupted).status_code==409
        mark('interrupted_request',{'visible_after_restart':True,'reexecution_blocked':True})
        assert db.museum_sources.count_documents({})==0
        backup=c.post('/api/museum/admin/backup',headers=admin,timeout=180);backup.raise_for_status();saved=backup.json()
        assert saved['status']=='completed';archive=work/'backups'/saved['archive'];assert archive.exists()
        restore_db='museum_restore_'+stamp
        # A fresh, empty isolated database is the restore target; never drop a user's database.
        assert restore_db not in client.list_database_names()
        proc=subprocess.run([args.restore,'--uri='+uri,'--archive='+str(archive),'--gzip','--nsFrom='+dbname+'.*','--nsTo='+restore_db+'.*','--stopOnError'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=flags,timeout=180)
        assert proc.returncode==0
        assert snapshot(client[restore_db])==saved['collections']
        for name in RUNTIME_COLLECTIONS:
            assert sorted(list(db[name].list_indexes()),key=lambda x:x['name'])==sorted(list(client[restore_db][name].list_indexes()),key=lambda x:x['name'])
        mark('backup_restore',{'archive_sha256':saved['archive_sha256'],'same_counts_ids_content_hashes':True,'collections':saved['collections'],'fresh_target':restore_db})
        mark('indexes',{'all_six_collection_indexes_restored':True,'session_ttl':True,'request_unique_and_ttl':True})
        assert c.get('/api/museum/admin/export',headers=admin).status_code==200
        mark('runtime_only',{'source_documents_in_mongo':0})
        report['passed']=True
    finally:
        stop(api);stop(mongo);client.close()
        for f in logs:f.close()
        report['finished_at']=time.time();report['workdir']=str(work)
        Path(args.output).write_text(json.dumps(report,indent=2),encoding='utf-8')
    return report

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--mongod',required=True);p.add_argument('--dump',required=True);p.add_argument('--restore',required=True);p.add_argument('--output',required=True)
    args=p.parse_args()
    if Path(args.output).exists():raise SystemExit('Choose a new report path')
    check(args)
