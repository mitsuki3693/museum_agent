"""Live proxy + MongoDB: 12 field answers, 3 scripts, 1 open question.

Only the open question uses the provider (at most six calls in the existing
bounded pipeline); all direct/script requests must report no model usage.
"""
import argparse
import json
from pathlib import Path
import time
from uuid import uuid4
import httpx
from pymongo import MongoClient
from app.museum.config import MuseumSettings, ROOT
from evaluate_bilingual_retrieval import private_path


def main(output):
    cfg=MuseumSettings()
    records=json.loads(cfg.museum_private_corpus.read_bytes())+json.loads(cfg.museum_corpus.read_bytes())
    source={r['_id']:r for r in records}
    report=dict(complete=False,rows=[])
    with private_path(output).open('x',encoding='utf-8') as out, MongoClient(cfg.mongodb_uri) as mongo, httpx.Client(
            base_url='http://127.0.0.1:3000',timeout=110,trust_env=False) as client:
        db=mongo[cfg.mongodb_db]
        def save():
            out.seek(0);json.dump(report,out,ensure_ascii=False,indent=2);out.truncate();out.flush()
        health=client.get('/api/museum/health');health.raise_for_status();report['health']=health.json()
        assert report['health']['catalogue_answers'] and report['health']['text_rerank']['backend']=='int8'
        assert report['health']['text_rerank']['state']=='ready'
        assert report['health']['corpus_count']==1012 and report['health']['storage']=='mongo'
        def session():
            r=client.post('/api/museum/sessions');r.raise_for_status()
            return {'Authorization':'Bearer '+r.json()['token']}
        def chat(headers,kind,sid,query,mode='brief',action='question'):
            body=dict(query=query,object_id=sid,mode=mode,action=action,request_id=str(uuid4()))
            start=time.perf_counter();r=client.post('/api/museum/chat',headers=headers,json=body);r.raise_for_status()
            result=r.json();trace=db.museum_traces.find_one({'_id':result['trace_id']});assert trace
            row=dict(kind=kind,object_id=sid,query=query,http_ms=round((time.perf_counter()-start)*1000),result=result,
                trace={k:trace.get(k) for k in ['_id','query','rewritten_query','retrieval_route','answer_route','catalogue_answer','timing','attempts','rerank']})
            report['rows'].append(row);save()
            print(json.dumps(dict(kind=kind,ms=row['http_ms'],status=result['status'],usage=len(result['usage']))),flush=True)
            return result,trace,body
        for sid in ['va-o77966-flower-pyramid','va-o7345','artic-16568','va-o496777']:
            headers=session()
            for field,query in [('author','作者是谁？'),('date','它是什么年代的？'),('material','它是什么材质？')]:
                result,trace,body=chat(headers,field,sid,query)
                assert result['status']=='answered' and not result['usage'] and not trace['attempts']
                assert trace['answer_route']=='catalogue_field' and trace['catalogue_answer']['field']==field
                assert result['claims'][0]['quote'] in source[sid]['content']
                assert trace['timing']['calls']['rewrite']==0 and trace['timing']['calls']['generation']==0
                if sid=='va-o77966-flower-pyramid' and field=='author':
                    repeat=client.post('/api/museum/chat',headers=headers,json=body);repeat.raise_for_status()
                    conflict=client.post('/api/museum/chat',headers=headers,json={**body,'query':'它是什么年代的？'})
                    assert repeat.json()['trace_id']==result['trace_id'] and conflict.status_code==409
                    feedback=client.post('/api/museum/feedback',headers=headers,json=dict(trace_id=result['trace_id'],kind='helpful',comment='开发验收：原文逐字段引用；非真实游客反馈'))
                    feedback.raise_for_status();assert db.museum_feedback.find_one({'trace_id':result['trace_id']})
                    other=session()
                    forbidden=client.post('/api/museum/feedback',headers=other,json=dict(trace_id=result['trace_id'],kind='helpful'))
                    assert forbidden.status_code==404
                    report['idempotency_conflict_feedback_isolation']=True
            if sid=='va-o77966-flower-pyramid':open_headers=headers
        for mode in ['brief','deep','children']:
            result,trace,_=chat(session(),'prepared_narration','va-neptune-triton','讲解这件作品',mode,'narration')
            assert result['status']=='answered' and not result['usage'] and result['narration']['prepared']
            assert trace['answer_route']=='prepared_narration' and trace['timing']['calls']['prepared_narration']==1
        result,trace,_=chat(open_headers,'open_question','va-o77966-flower-pyramid','这种花器为什么做成塔形？')
        assert trace['answer_route']=='model_or_discovery'
        assert trace['timing']['calls']['fact_selection']==1
        if result['status']=='answered':
            assert trace['timing']['calls']['verification']>=1 and result['verification']['passed']
        report['complete']=True;save()
    print(json.dumps(dict(complete=True,tasks=len(report['rows']),open_status=result['status'])),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True)
    main(p.parse_args().output)
