"""Fixed selected-work phase timing. Private text; at most 24 real API calls.

Baseline uses the configured provider. Candidate field answers are deterministic;
the open-question control replays captured replies with identical message hashes.
This isolates answer generation, not unselected-work retrieval or HTTP latency.
"""
import argparse
import asyncio
import copy
import hashlib
import json
from pathlib import Path
import time

from app.museum.config import MuseumSettings, ROOT
from app.museum.engine import MuseumEngine
from app.storage.store import MemoryStore
from app.llm.client import LLMError
from evaluate_bilingual_retrieval import private_path, versions

CASES = [('author','作者是谁？'), ('date','它是什么年代的？'), ('material','它是什么材质？'),
         ('open','这种花器为什么做成塔形？')]


def sha(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True).encode()).hexdigest()


class SelectedIndex:
    def __init__(self, record, corpus_hash):
        self.records={record['_id']:copy.deepcopy(record)}
        self.corpus_hash=corpus_hash

    def exact_accession_ids(self, query):return []

    async def search_for_answer(self, query, object_id, variant):
        assert object_id in self.records
        return [copy.deepcopy(self.records[object_id])],dict(status='selected_object')


def freeze(path):
    cfg=MuseumSettings()
    records=json.loads(cfg.museum_private_corpus.read_bytes())
    source=next(r for r in records if r['_id']=='va-o77966-flower-pyramid')
    raw=cfg.museum_corpus.read_bytes()+b'\n'+cfg.museum_private_corpus.read_bytes()
    fixture=dict(source=source,versions=versions(cfg),corpus_hash=hashlib.sha256(raw).hexdigest(),
        model=cfg.deepseek_model,prompt_policy=cfg.museum_answer_policy,verifier_policy=cfg.museum_verifier_policy,
        bounded=cfg.museum_rewrite_overlong_answers,cases=CASES,
        history=[dict(role='user',content='我已经选定这件金字塔形花器，接下来询问这件作品。')])
    with private_path(path).open('x',encoding='utf-8') as f:json.dump(fixture,f,ensure_ascii=False,indent=2)
    print('Frozen four selected-work questions; no API calls.')


async def run(fixture_path, output):
    fixture=json.loads(private_path(fixture_path).read_bytes())
    cfg=MuseumSettings()
    assert cfg.deepseek_api_key and cfg.deepseek_model==fixture['model'] and versions(cfg)==fixture['versions']
    assert cfg.museum_answer_policy==fixture['prompt_policy'] and cfg.museum_verifier_policy==fixture['verifier_policy']
    assert cfg.museum_rewrite_overlong_answers==fixture['bounded']
    report=dict(complete=False,fixture_sha256=hashlib.sha256(fixture_path.read_bytes()).hexdigest(),
        api_call_limit=24,actual_api_calls=0,rows=[])
    with private_path(output).open('x',encoding='utf-8') as out:
        def save():
            out.seek(0);json.dump(report,out,ensure_ascii=False,indent=2);out.truncate();out.flush()
        for ident,query in fixture['cases']:
            replies=[]
            for enabled in [False,True]:
                settings=cfg.model_copy(update=dict(museum_catalogue_answers=enabled))
                store=MemoryStore();source=copy.deepcopy(fixture['source'])
                await store.upsert('museum_sources',source)
                engine=MuseumEngine(settings,store,SelectedIndex(source,fixture['corpus_hash']))
                client=engine._client();original=client.complete_json
                cursor=0
                async def complete(messages):
                    nonlocal cursor
                    digest=sha(messages)
                    if enabled:
                        prior=replies[cursor];cursor+=1
                        assert prior['messages_sha256']==digest,'Changed fallback prompt'
                        if 'error' in prior:raise LLMError('Captured provider failure')
                        return copy.deepcopy(prior['response'])
                    assert report['actual_api_calls']<report['api_call_limit']
                    report['actual_api_calls']+=1
                    try:
                        response=await original(messages)
                        replies.append(dict(messages_sha256=digest,response=copy.deepcopy(response)))
                        return response
                    except Exception as exc:
                        replies.append(dict(messages_sha256=digest,error=type(exc).__name__))
                        raise
                client.complete_json=complete;engine.client_factory=lambda:client
                started=time.perf_counter()
                result=await engine.answer(query,dict(_id=f'{ident}-{enabled}',history=copy.deepcopy(fixture['history'])),
                                           'brief',source['_id'])
                trace=await store.get('museum_traces',result['trace_id'])
                row=dict(id=ident,enabled=enabled,replayed=enabled and ident=='open',result=result,trace=trace,
                         wall_ms=round((time.perf_counter()-started)*1000),provider_calls=0 if enabled else len(replies))
                if not enabled:row['responses']=copy.deepcopy(replies)
                elif ident!='open':
                    assert cursor==0 and result['status']=='answered' and not result['usage']
                    assert result['claims'][0]['quote'] in source['content']
                else:
                    before=report['rows'][-1]['result']
                    assert cursor==len(replies)
                    assert all(result[k]==before[k] for k in ['status','answer','claims','verification'])
                    row['same_output_and_messages']=True
                report['rows'].append(row);save()
                print(json.dumps(dict(id=ident,enabled=enabled,status=result['status'],ms=row['wall_ms'],calls=row['provider_calls'])),flush=True)
        report['complete']=True;save()


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode',choices=['freeze','run']);p.add_argument('--fixture',type=Path,required=True)
    p.add_argument('--output',type=Path)
    args=p.parse_args()
    if args.mode=='freeze':freeze(args.fixture)
    else:
        if not args.output:raise ValueError('Output required')
        asyncio.run(run(args.fixture,args.output))
