"""Controlled A/B/B2/C developer evaluation, three repeats, no automatic retry.

Uses the same six previously approved public-source photos and fixed visual Top3.
At most 150 model calls, two independent cases at a time. All artifacts private.
"""
import asyncio
import hashlib
import json
from pathlib import Path
import sys
import time
from app.museum.config import MuseumSettings
from app.museum.visual_index import MuseumVisualIndex
from app.museum.local_correspondence import LocalCorrespondence
from app.museum.engine import MuseumEngine
from app.museum.vision import PhotoRecognizer
from app.storage.store import MemoryStore

ROOT=Path(__file__).resolve().parents[1]

async def main():
    cfg=MuseumSettings();p=ROOT/'data/private';out=ROOT/'eval/private/partial-regions-v1/live.json'
    assert not out.exists(), 'Never overwrite evaluation results'
    records={r['_id']:r for path in [cfg.museum_corpus,cfg.museum_private_corpus] for r in json.loads(path.read_bytes())}
    store=MemoryStore()
    for row in records.values():await store.upsert('museum_sources',row)
    visual=MuseumVisualIndex(cfg.museum_visual_manifest,cfg.museum_visual_model,records,cache_dir=cfg.museum_visual_cache)
    await visual.start();visual.local_correspondence=LocalCorrespondence(visual,cfg.museum_visual_cache)
    cases=json.loads((p/'MUSE-test-pack-12-20261006/manifest.json').read_bytes())
    report=dict(scope='Known developer images; fixed visual top3; three repeats; no retries; not online AB',model=cfg.deepseek_model,
                index_hash=visual.index_hash, corpus_hash=hashlib.sha256(cfg.museum_private_corpus.read_bytes()).hexdigest(),results=[],observations={})
    class Index:
        async def search(self,query):return []  # Same visual-only candidate set in every arm.
    index=Index();index.records=records;index.corpus_hash=report['corpus_hash']
    semaphore=asyncio.Semaphore(2)
    async def run_case(n):
        async with semaphore:
            case=cases[n-1];raw=(p/'MUSE-test-pack-12-20261006'/case['file']).read_bytes()
            assert hashlib.sha256(raw).hexdigest()==case['sha256']
            frozen={};engine=MuseumEngine(cfg,store,index);engine.visual_index=visual
            for repeat in range(3):
                modes=['legacy','visibility','candidate','partial']
                shift=(repeat+n)%4;modes=modes[shift:]+modes[:shift]
                for mode in modes:
                    real=engine._client()
                    class Replay:
                        @property
                        def usage_records(self):return real.usage_records
                        async def complete_json(self,messages):
                            observe=messages[0]['content'].startswith('只描述照片')
                            if observe and 'value' in frozen:return frozen['value']
                            value=await real.complete_json(messages)
                            if observe:frozen['value']=value
                            return value
                    engine.client_factory=Replay
                    engine.settings=cfg.model_copy(update={'museum_photo_verification':mode,'museum_photo_reference_mode':'single'})
                    started=time.perf_counter()
                    result=await PhotoRecognizer(engine).recognize(raw,{'_id':f'isolated-region-{n}-{repeat}-{mode}'})
                    trace=await store.get('museum_photo_traces',result['trace_id'])
                    report['results'].append(dict(case=n,repeat=repeat,mode=mode,expected=case['expected_source_id'],result=result,trace=trace,ms=round((time.perf_counter()-started)*1000)))
                    report['observations'][str(n)]=hashlib.sha256(json.dumps(frozen,sort_keys=True).encode()).hexdigest()
                    out.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
                    print(n,repeat,mode,result['status'],trace['candidate_ids'],trace['error'],flush=True)
                    if 'value' not in frozen:
                        print('Observation failed; no automatic retry for case',n,flush=True);return
    await asyncio.gather(*(run_case(n) for n in [1,4,5,8,11,12]))
    for n in [1,4,5,8,11,12]:
        rows=[r['trace'] for r in report['results'] if r['case']==n and r['trace']['compared_ids']]
        assert all(r['compared_ids']==rows[0]['compared_ids'] and r['visual_scores']==rows[0]['visual_scores'] for r in rows)
    report['complete']=True
    out.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print('Completed partial verification experiment:',len(report['results']),'runs')

if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8');asyncio.run(main())
