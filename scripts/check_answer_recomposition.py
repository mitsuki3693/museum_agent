"""Replay the frozen overlong draft, then use real generation/verification.

Three fixed runs, at most six real text calls. Cached first steps are explicitly
marked; this is not an end-to-end or latency benchmark. Private reports only.
"""
import asyncio
import copy
import hashlib
import json
from pathlib import Path
import time

from app.museum.config import MuseumSettings
from app.museum.engine import MuseumEngine
from app.storage.store import MemoryStore
from check_origin_followups import SelectedIndex


async def run(output):
    fixture_path = Path('eval/private/fact-selection-input-v1.json')
    baseline_path = Path('eval/private/fact-selection-repeat-v1.json')
    fixture = json.loads(fixture_path.read_text(encoding='utf-8'))
    baseline = json.loads(baseline_path.read_text(encoding='utf-8'))
    case = next(c for c in fixture['cases'] if c['name']=='jug-premise')
    row = next(r for r in baseline['rows'] if r['case']=='jug-premise')
    prefix = [copy.deepcopy(c['response']) for c in row['calls'][:3]]
    assert len(prefix[2]['claims']) > 2
    config = MuseumSettings()
    if not config.deepseek_api_key or config.deepseek_model != fixture['model']:
        raise ValueError('Frozen model and configured credentials required')
    if output.exists() or not output.resolve().is_relative_to(Path('eval/private').resolve()):
        raise ValueError('Use a new private report')
    config.museum_text_rerank = config.museum_chinese_recall = False
    config.museum_search_fields = None
    config.museum_answer_policy = 'facts'
    config.museum_rewrite_overlong_answers = True
    results = []
    report = {'scope':'cached-rewrite-selection-first-draft-live-recomposition-and-review',
        'fixture_sha256':hashlib.sha256(fixture_path.read_bytes()).hexdigest(),
        'baseline_sha256':hashlib.sha256(baseline_path.read_bytes()).hexdigest(), 'rows':results}
    with output.open('x',encoding='utf-8') as stream:
        for repetition in range(3):
            store = MemoryStore()
            engine = MuseumEngine(config,store,SelectedIndex({**fixture,'sources':case['sources']}))
            client = engine._client()
            complete = client.complete_json
            calls = []
            async def measured(messages):
                cached = len(calls) < 3
                entry = {'cached':cached, 'system_sha256':hashlib.sha256(messages[0]['content'].encode()).hexdigest()}
                index = len(calls)
                calls.append(entry)
                started = time.perf_counter()
                try:
                    response = copy.deepcopy(prefix[index]) if cached else await complete(messages)
                    entry['response'] = response
                    return response
                except Exception as exc:
                    entry['error'] = type(exc).__name__
                    raise
                finally:
                    entry['ms'] = round((time.perf_counter()-started)*1000)
            client.complete_json = measured
            engine.client_factory = lambda:client
            result = await engine.answer(case['query'],{'_id':f'length-{repetition}',
                'history':copy.deepcopy(case['history'])},'brief',case['object_id'])
            trace = await store.get('museum_traces',result['trace_id'])
            assert trace['attempts'][1]['draft'] == prefix[2]
            assert trace['attempts'][1]['omitted_claims'] == 0
            assert trace['attempts'][1]['verdict'] is None
            results.append({'repetition':repetition,'calls':calls,'trace':trace})
            stream.seek(0)
            json.dump(report,stream,ensure_ascii=False,indent=2)
            stream.truncate()
            stream.flush()
            print(json.dumps({'repetition':repetition,'status':result['status'],
                'real_calls':sum(not c['cached'] for c in calls)}),flush=True)
    print('sha256',hashlib.sha256(output.read_bytes()).hexdigest())


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    asyncio.run(run(parser.parse_args().output))
