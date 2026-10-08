"""Eight frozen development tasks, paired legacy/facts (maximum 88 text calls).

Public-source text only; no pictures. Reports remain private. Provider errors
are recorded without retry; engine retains its single draft repair attempt.
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

INPUT = Path('eval/private/fact-selection-input-v1.json')
COMBINED_CASES = 'jug-premise,bottle-premise,bottle-style,bottle-unknown'


def freeze(config):
    old = json.loads(Path('eval/private/origin-followups-input-v1.json').read_text(encoding='utf-8'))
    records = json.loads(config.museum_private_corpus.read_text(encoding='utf-8'))
    bottle = next(s for s in records if s['_id'] == 'va-o496777')
    cases = [{'name':'jug-'+name,'query':query,'expected_status':expected,
              'object_id':old['object_id'],'sources':old['sources'],'history':old['history']}
             for name, query, expected in old['cases']]
    for name, query, expected in [
        ('china','它是在中国制作的吗？','answered'),
        ('premise','既然瓶身在代尔夫特制作，为什么资料写中国？','answered'),
        ('style','柿右卫门风格是不是说明这件瓶子在日本制作？','answered'),
        ('unknown','这件瓶子的制作者当天早餐吃了什么？','insufficient_evidence'),
    ]:
        cases.append({'name':'bottle-'+name,'query':query,'expected_status':expected,
            'object_id':bottle['_id'],'sources':[bottle],
            'history':[{'role':'user','content':'我们来聊这件瓶子。'}]})
    save_new(INPUT, {'model':config.deepseek_model,'corpus_hash':old['corpus_hash'], 'cases':cases})


async def run(config, output, policy_filter=None, case_filter=None, combined=False):
    if combined:
        if policy_filter or case_filter or config.museum_rewrite_overlong_answers:
            raise ValueError('Combined comparison uses its own frozen cases and settings')
        case_filter = COMBINED_CASES
    if output.exists() or not output.resolve().is_relative_to(Path('eval/private').resolve()):
        raise ValueError('Use a new report path under eval/private')
    fixture = json.loads(INPUT.read_text(encoding='utf-8'))
    cases = fixture['cases']
    if case_filter:
        wanted = set(case_filter.split(','))
        if not wanted.issubset({c['name'] for c in cases}):
            raise ValueError('Unknown case name')
        cases = [c for c in cases if c['name'] in wanted]
    if not config.deepseek_api_key or config.deepseek_model != fixture['model']:
        raise ValueError('Frozen model and configured key required')
    config.museum_text_rerank = False
    config.museum_chinese_recall = False
    config.museum_search_fields = None
    rows = []
    report = {'scope':'two-objects-development-paired-not-production-or-accuracy',
        'input_sha256':hashlib.sha256(INPUT.read_bytes()).hexdigest(), 'model':config.deepseek_model,
        'selected_cases':[c['name'] for c in cases], 'policy_filter':policy_filter,
        'comparison':('legacy-vs-combined' if combined else 'single-'+policy_filter if policy_filter else 'legacy-vs-facts'),
        'model_parameters':{'temperature':0,'thinking':'disabled','timeout_seconds':25,'max_tokens':1800},
        'max_provider_calls':44 if combined else (6 if policy_filter else 11)*len(cases),
        'requested_rewrite_overlong_answers':config.museum_rewrite_overlong_answers,
        'rows':rows}
    # Reserve before incurring any API cost. Checkpoint after every completed task.
    with output.open('x', encoding='utf-8') as stream:
        for i, case in enumerate(cases):
            new_policy = 'combined' if combined else 'facts'
            for policy in ([policy_filter] if policy_filter else
                           ['legacy',new_policy] if i%2 == 0 else [new_policy,'legacy']):
                # Isolate each arm from previous rows and local experimental env settings.
                row_config = config.model_copy(deep=True)
                row_config.museum_answer_policy = 'facts' if policy=='combined' else policy
                row_config.museum_verifier_policy = 'entailment_v1' if policy=='combined' else 'legacy'
                if combined or policy=='combined':
                    row_config.museum_rewrite_overlong_answers = policy=='combined'
                store = MemoryStore()
                engine = MuseumEngine(row_config, store, SelectedIndex({**fixture, 'sources':case['sources']}))
                client = engine._client()
                real_complete = client.complete_json
                calls = []
                async def measured(messages):
                    start = time.perf_counter()
                    entry = {'system_sha256':hashlib.sha256(messages[0]['content'].encode()).hexdigest()}
                    try:
                        value = await real_complete(messages)
                        entry['response'] = value
                        return value
                    except Exception as exc:
                        entry['error'] = type(exc).__name__
                        raise
                    finally:
                        entry['ms'] = round((time.perf_counter()-start)*1000)
                        calls.append(entry)
                client.complete_json = measured
                engine.client_factory = lambda:client
                result = await engine.answer(case['query'], {'_id':case['name'],
                    'history':copy.deepcopy(case['history'])}, 'brief', case['object_id'])
                trace = await store.get('museum_traces', result['trace_id'])
                rows.append({'case':case['name'], 'policy':policy, 'expected_status':case['expected_status'],
                    'settings':{'answer_policy':row_config.museum_answer_policy,
                                'verifier_policy':row_config.museum_verifier_policy,
                                'rewrite_overlong_answers':row_config.museum_rewrite_overlong_answers},
                    'calls':calls, 'trace':trace, 'human_review':'pending'})
                stream.seek(0)
                json.dump(report, stream, ensure_ascii=False, indent=2)
                stream.truncate()
                stream.flush()
                print(json.dumps({'case':case['name'],'policy':policy,'status':result['status'],
                    'ms':result['latency_ms'],'calls':len(calls)}),flush=True)
    print('sha256', hashlib.sha256(output.read_bytes()).hexdigest())


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--freeze',action='store_true')
    parser.add_argument('--output',type=Path)
    parser.add_argument('--policy', choices=['legacy','facts','combined'])
    parser.add_argument('--cases', help='Comma-separated frozen case names; no new questions')
    parser.add_argument('--rewrite-overlong', action='store_true')
    parser.add_argument('--compare-combined', action='store_true',
                        help='Four frozen tasks, legacy versus facts+bounded+entailment; at most 44 calls')
    args = parser.parse_args()
    config = MuseumSettings()
    config.museum_rewrite_overlong_answers = args.rewrite_overlong
    if args.freeze:
        freeze(config)
    elif args.output:
        asyncio.run(run(config,args.output,args.policy,args.cases,args.compare_combined))
    else:
        parser.error('--freeze or --output is required')
