"""Six fresh text tasks through the local web proxy, without automatic retries.

Public catalogue queries only. Mongo traces and raw reports remain private.
The first five tasks check presentation, not merely offline top-five retrieval.
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
    cfg = MuseumSettings()
    assert cfg.museum_catalogue_glossary and cfg.museum_fallback_glossary
    report = dict(complete=False, scope='Developer text HTTP smoke; not online A/B or visitor accuracy', rows=[])
    cases = [
        ('glass','找彭毅 Roles 系列 No.5 的玻璃王冠雕塑','va-o1803107'),
        ('sappho','找低着头的黑色石膏萨福半身像','va-o1388779'),
        ('chair','找红漆椅子，椅背中央有蝙蝠和云纹','va-o491703'),
        ('christ','霍斯金斯和格兰特给韦奇伍德供模的那件基督石膏像','va-o1401986'),
        ('jerome','陶土半身像，张着嘴仰头向左看','va-o311495'),
        ('ambiguous','黄色背景上画橙色蔓草纹的长方形地砖碎片',None),
    ]
    with output.open('x',encoding='utf-8') as f, httpx.Client(
        base_url='http://127.0.0.1:3000',timeout=120,trust_env=False) as client, MongoClient(cfg.mongodb_uri) as mongo:
        db = mongo[cfg.mongodb_db]
        health = client.get('/api/museum/health'); health.raise_for_status()
        report['health'] = health.json()
        assert report['health']['corpus_count'] == 1012
        for name, query, gold in cases:
            session = client.post('/api/museum/sessions'); session.raise_for_status()
            headers = {'Authorization':'Bearer '+session.json()['token']}
            start = time.perf_counter()
            response = client.post('/api/museum/chat',headers=headers,
                json=dict(query=query,request_id=str(uuid4())))
            row = dict(name=name,query=query,gold=gold,http_status=response.status_code,
                       ms=round((time.perf_counter()-start)*1000))
            if response.status_code == 200:
                result = response.json()
                trace = db.museum_traces.find_one({'_id':result['trace_id']})
                assert trace is not None, 'Trace was not persisted'
                row.update(result=result,rerank=trace.get('rerank'),
                    gold_retrieved=gold in result.get('retrieved_ids',[]) if gold else None,
                    gold_presented=gold in [c['id'] for c in result.get('candidates',[])] if gold else None,
                    calls=len(result.get('usage',[])))
            else:
                row['service_error'] = response.text[:150]
            report['rows'].append(row)
            f.seek(0);json.dump(report,f,ensure_ascii=False,indent=2);f.truncate();f.flush()
            print(json.dumps({k:v for k,v in row.items() if k not in ['query','result','rerank']},ensure_ascii=False),flush=True)
        report['complete'] = True
        f.seek(0);json.dump(report,f,ensure_ascii=False,indent=2);f.truncate()


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    main(parser.parse_args().output)
