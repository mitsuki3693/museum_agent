"""Small private, real-provider follow-up regression; no retrieval/model changes.

Freeze an explicit local browser trace before running. Four cases, at most twenty
provider calls, no transport retries. Reports include source text: keep private.
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

FIXTURE = Path('eval/private/origin-followups-input-v1.json')
CASES = [
    ('china', '它是在中国制作的吗？', 'answered'),
    ('delft', '它是在代尔夫特制作的吗？', 'answered'),
    ('premise', '既然它在中国制作，为什么馆方写着代尔夫特？', 'answered'),
    ('unknown', '这件壶的制作者当天早餐吃了什么？', 'insufficient_evidence'),
]

class SelectedIndex:
    def __init__(self, fixture):
        self.corpus_hash = fixture['corpus_hash']
        self.sources = fixture['sources']

    def exact_accession_ids(self, query):
        return []

    async def search(self, query, object_id, variant):
        return copy.deepcopy(self.sources)

def save_new(path, value):
    if not path.resolve().is_relative_to(Path('eval/private').resolve()):
        raise ValueError('Report must remain in eval/private')
    with path.open('x', encoding='utf-8') as f:
        json.dump(value, f, ensure_ascii=False, indent=2)

def freeze(settings):
    from pymongo import MongoClient
    with MongoClient(settings.mongodb_uri) as client:
        db = client[settings.mongodb_db]
        failed = db.museum_traces.find_one({'_id':'b172795c78d540f9b8e4b5601e6a690b'})
        if not failed:
            raise ValueError('Original failure trace missing')
        history = []
        for trace_id in ['32a96a63f2894fa7a8e90dac1d4e2c74', '0fe7e15ff5fb47839122339f03015955']:
            row = db.museum_traces.find_one({'_id':trace_id})
            history += [{'role':'user','content':row['query']},
                        {'role':'assistant','content':row['result']['answer']}]
        sources = []
        for source in failed['result']['sources']:
            source = dict(source)
            source['_id'] = source.pop('id')
            sources.append(source)
        save_new(FIXTURE, {'origin_trace':failed['_id'], 'corpus_hash':failed['corpus_hash'],
            'model':failed['model'], 'sources':sources, 'object_id':failed['object_id'],
            'history':history, 'cases':CASES, 'original_attempts':failed['attempts']})

async def run(settings, output, replay=None):
    if not output.resolve().is_relative_to(Path('eval/private').resolve()):
        raise ValueError('Report must remain in eval/private')
    if output.exists():
        raise FileExistsError(output)
    fixture = json.loads(FIXTURE.read_text(encoding='utf-8'))
    if not replay and (settings.deepseek_model != fixture['model'] or not settings.deepseek_api_key):
        raise ValueError('Use the frozen configured model and credentials')
    # Hold the selected source fixed: this test isolates generation and repair.
    settings.museum_text_rerank = False
    settings.museum_chinese_recall = False
    settings.museum_search_fields = None
    rows = []
    frozen = json.loads(replay.read_text(encoding='utf-8')) if replay else None
    for case_index, (name, query, expected) in enumerate(fixture['cases']):
        store = MemoryStore()
        engine = MuseumEngine(settings, store, SelectedIndex(fixture))
        client = engine._client()
        original_complete = client.complete_json
        if frozen:
            from app.llm.client import LLMError
            previous = frozen['rows'][case_index]
            replies = [{'query': previous['trace']['rewritten_query']}]
            for attempt in previous['trace']['attempts']:
                if attempt.get('error'):
                    replies.append(LLMError('Replayed provider failure'))
                elif attempt.get('status') == 'abstained':
                    replies.append({'abstain':True, 'claims':[]})
                else:
                    replies.append(attempt['draft'])
                    if attempt.get('verdict') is not None:
                        replies.append(attempt['verdict'])
            responses = iter(replies)
            async def original_complete(messages):
                response = next(responses)
                if isinstance(response, Exception):
                    raise response
                return copy.deepcopy(response)
        calls = []
        async def measured(messages):
            start = time.perf_counter()
            try:
                return await original_complete(messages)
            finally:
                calls.append({'system_sha256':hashlib.sha256(messages[0]['content'].encode()).hexdigest(),
                              'ms':round((time.perf_counter()-start)*1000)})
        client.complete_json = measured
        engine.client_factory = lambda: client
        result = await engine.answer(query, {'_id':name,'history':copy.deepcopy(fixture['history'])},
                                     'brief', fixture['object_id'])
        trace = await store.get('museum_traces', result['trace_id'])
        if frozen:
            assert [c['system_sha256'] for c in calls] == [c['system_sha256'] for c in previous['calls']]
            assert result['status'] == previous['trace']['result']['status']
            assert result['answer'] == previous['trace']['result']['answer']
        rows.append({'case':name,'expected_status':expected,'status_matches':result['status']==expected,
                     'calls':calls,'trace':trace})
        print(json.dumps({'case':name,'status':result['status'],'ms':result['latency_ms'],
                          'calls':len(calls)},ensure_ascii=True), flush=True)
    save_new(output, {'scope':('cached-response-prompt-parity-no-provider' if frozen else
                             'fixed-selected-source-real-provider-not-browser-or-accuracy'),
        'fixture_sha256':hashlib.sha256(FIXTURE.read_bytes()).hexdigest(),
        'prompt_version':engine.PROMPT_VERSION,'model':settings.deepseek_model,'rows':rows})
    print('sha256', hashlib.sha256(output.read_bytes()).hexdigest())
    if not frozen:
        assert all(row['status_matches'] for row in rows), 'Follow-up status regression; inspect private drafts'

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--freeze', action='store_true')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--policy', choices=['legacy', 'focus', 'repair', 'geography'], default='legacy')
    parser.add_argument('--replay', type=Path, help='Replay a private recorded run without provider calls')
    args = parser.parse_args()
    config = MuseumSettings()
    config.museum_answer_policy = args.policy
    if args.freeze:
        freeze(config)
    else:
        if args.output is None:
            parser.error('--output is required')
        asyncio.run(run(config, args.output, args.replay))
