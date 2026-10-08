"""Replay the observed lossy search rewrite, then run real answer stages.

Two unknown-information repetitions and one answerable maker control; at most
15 real text calls. All sources, history, responses and reports stay private.
"""
import argparse
import asyncio
import copy
import hashlib
import json
from pathlib import Path
import time

from app.museum.config import MuseumSettings
from app.museum.engine import MuseumEngine
from app.storage.store import MemoryStore
from check_origin_followups import SelectedIndex, save_new


async def run(output):
    web_path = Path('eval/private/source-wording-web-trial-v1.json')
    source_path = Path('eval/private/fact-selection-input-v1.json')
    web = json.loads(web_path.read_text(encoding='utf-8'))
    fixture = json.loads(source_path.read_text(encoding='utf-8'))
    original = next(c for c in fixture['cases'] if c['name']=='jug-premise')
    failed = web['rows'][-1]
    history = []
    for row in web['rows'][:-1]:
        history += [{'role':'user','content':row['query']},
                    {'role':'assistant','content':row['result']['answer']}]
    frozen = {'sources':original['sources'],'history':history[-6:],
              'cached_rewrite':{'query':failed['rewritten_query']},
              'cases':[('unknown-a',failed['query']),('unknown-b',failed['query']),
                       ('maker-control','这件执壶的制作者是谁？')]}
    config = MuseumSettings()
    if not config.deepseek_api_key or config.deepseek_model != fixture['model']:
        raise ValueError('Frozen model and configured key required')
    config.museum_answer_policy = 'facts'
    config.museum_rewrite_overlong_answers = True
    config.museum_verifier_policy = 'entailment_v1'
    config.museum_chinese_recall = config.museum_text_rerank = False
    config.museum_search_fields = None
    report = {'scope':'cached-real-lossy-rewrite-live-answer-development-check',
        'web_report_sha256':hashlib.sha256(web_path.read_bytes()).hexdigest(),
        'source_fixture_sha256':hashlib.sha256(source_path.read_bytes()).hexdigest(),
        'input':frozen,'max_real_calls':15,'human_review':'pending','rows':[]}
    save_new(output, report)  # Reserve a private, non-overwritable path before API cost.
    for name, query in frozen['cases']:
        store = MemoryStore()
        engine = MuseumEngine(config,store,SelectedIndex({**fixture,'sources':frozen['sources']}))
        client = engine._client()
        complete = client.complete_json
        calls = []
        async def measured(messages):
            cached = not calls
            entry = {'cached':cached,'system_sha256':hashlib.sha256(messages[0]['content'].encode()).hexdigest()}
            calls.append(entry)
            started = time.perf_counter()
            try:
                value = copy.deepcopy(frozen['cached_rewrite']) if cached else await complete(messages)
                entry['response'] = value
                return value
            except Exception as exc:
                entry['error'] = type(exc).__name__
                raise
            finally:
                entry['ms'] = round((time.perf_counter()-started)*1000)
        client.complete_json = measured
        engine.client_factory = lambda:client
        result = await engine.answer(query,{'_id':name,'history':copy.deepcopy(history[-6:])},'brief',original['object_id'])
        trace = await store.get('museum_traces',result['trace_id'])
        report['rows'].append({'case':name,'calls':calls,'trace':trace})
        output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'case':name,'status':result['status'],'real_calls':sum(not c['cached'] for c in calls)}),flush=True)
    print('sha256',hashlib.sha256(output.read_bytes()).hexdigest())


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    asyncio.run(run(parser.parse_args().output))
