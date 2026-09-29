"""Paired offline experiment, not a visitor A/B test.

Freeze one actual image observation per query and replay it into both arms.
Both arms use identical models and comparison instructions. The intervention is
visual candidate retrieval plus reference images. No ground truth enters prompts.
"""
import argparse
import asyncio
import hashlib
import json
import time
from pathlib import Path
from app.museum.config import MuseumSettings, ROOT
from app.museum.engine import MuseumEngine
from app.museum.retrieval import MuseumIndex
from app.museum.visual_index import MuseumVisualIndex, MODEL_ID, MODEL_REVISION, PREPROCESS_VERSION
from app.museum.vision import PhotoRecognizer
from app.storage.store import MemoryStore


async def run(args):
    output = Path(args.output)
    if output.exists():
        raise ValueError("Refusing to overwrite evaluation evidence")
    output.parent.mkdir(parents=True, exist_ok=True)
    cases_raw = Path(args.cases).read_bytes()
    cases = json.loads(cases_raw)
    saved_observations = {}
    if args.observation_report:
        for row in json.loads(Path(args.observation_report).read_bytes())['results']:
            if row.get('observation'):
                saved_observations[row['case']['sha256']] = row['observation'][0]
    config = MuseumSettings()
    store = MemoryStore()
    index = MuseumIndex(config, store)
    await index.start()
    visual = MuseumVisualIndex(Path(args.references), config.museum_visual_model, index.records)
    await visual.start()
    gallery = json.loads(Path(args.references).read_bytes())['references']
    ref_hashes = {r['sha256'] for r in gallery}
    if not 1 <= len(cases) <= 30:
        raise ValueError("Bounded evaluation accepts 1-30 cases")
    for case in cases:
        if case['split'] == 'held_out' and case['sha256'] in ref_hashes:
            raise ValueError("Query/reference overlap in held-out split")
        if hashlib.sha256((ROOT / case['path']).read_bytes()).hexdigest() != case['sha256']:
            raise ValueError("Changed input fixture")
    engine = MuseumEngine(config, store, index)
    factory = engine.client_factory
    report = dict(kind='paired offline diagnostic; no visitor traffic or general accuracy claim',
                  model=config.deepseek_model, prompt=PhotoRecognizer.PROMPT_VERSION,
                  visual_model=MODEL_ID, visual_revision=MODEL_REVISION, preprocessing=PREPROCESS_VERSION,
                  cases_hash=hashlib.sha256(cases_raw).hexdigest(), index_hash=visual.index_hash,
                  corpus_hash=index.corpus_hash, references=len(gallery),
                  visual_works=len({r['source_id'] for r in gallery}), results=[])
    report['observation_report'] = args.observation_report
    for case in cases:
        raw = (ROOT / case['path']).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == case['sha256']
        observation = [saved_observations[case['sha256']]] if case['sha256'] in saved_observations else []
        row = dict(case=case, arms={})
        for arm in (['caption_text', 'image_and_text'] if args.paired else ['image_and_text']):
            class SharedObservation:
                def __init__(self):
                    self.client, self.calls = factory(), 0
                @property
                def usage_records(self):
                    return self.client.usage_records
                async def complete_json(self, messages):
                    self.calls += 1
                    if self.calls == 1 and observation:
                        return observation[0]
                    value = await self.client.complete_json(messages)
                    if self.calls == 1:
                        observation.append(value)
                    return value
            engine.client_factory = SharedObservation
            engine.visual_index = visual if arm == 'image_and_text' else None
            started = time.perf_counter()
            result = await PhotoRecognizer(engine).recognize(raw, {'_id': 'offline-visual-evaluation'})
            trace = await store.get('museum_photo_traces', result['trace_id'])
            ids = [c['id'] for c in result['candidates']]
            expected = case.get('expected_id')
            passed = (ids == [expected]) if expected else (result['status'] == 'not_matched' and not ids)
            row['arms'][arm] = dict(status=result['status'], ids=ids, passed=passed,
                                    elapsed_s=round(time.perf_counter()-started, 3), trace=trace)
            print(json.dumps(dict(case=case['id'], arm=arm, status=result['status'], ids=ids, passed=passed)), flush=True)
        # Local report only: no image bytes or request credentials retained.
        row['observation'] = observation
        row['paired_comparable'] = args.paired and all(v['status'] != 'service_unavailable' for v in row['arms'].values())
        report['results'].append(row)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    failures=[r['case']['id'] for r in report['results'] if not r['arms']['image_and_text']['passed']]
    print('VISUAL_FAILURES', json.dumps(failures), flush=True)
    return bool(failures)


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases', required=True)
    parser.add_argument('--references', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--paired', action='store_true')
    parser.add_argument('--observation-report', help='Replay saved image observation, joined by exact image hash')
    raise SystemExit(asyncio.run(run(parser.parse_args())))
