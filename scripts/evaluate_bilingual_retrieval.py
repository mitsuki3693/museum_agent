"""Freeze real MiniLM candidates, then replay the production text search path.

All artifacts stay in eval/private. Replays change only the catalogue glossary;
no answer API, neural reranker, image path, source text or gold labels change.
"""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import time

from app.museum.config import MuseumSettings, ROOT
from app.museum.retrieval import MuseumIndex
from app.storage.store import MemoryStore


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def private_path(path):
    path = path.resolve()
    if not path.is_relative_to((ROOT/'eval/private').resolve()):
        raise ValueError('Reports must stay in eval/private')
    return path


def settings(local=False):
    return MuseumSettings(_env_file=None, deepseek_api_key='', museum_storage='memory',
        museum_corpus=ROOT/'data/corpus.json',
        museum_private_corpus=ROOT/'data/private/va-pilot-1000-v1-corpus.json',
        museum_search_fields=ROOT/'data/private/chinese-recall-scale-1000-v1.json',
        museum_search_allow_drafts=True, museum_chinese_recall=True,
        museum_fallback_glossary=True, museum_catalogue_glossary=False,
        museum_embedding='local' if local else 'lexical',
        museum_text_rerank=False)


def versions(cfg):
    return {name:digest(getattr(cfg, name)) for name in
            ['museum_corpus', 'museum_private_corpus', 'museum_search_fields']}


def rank(ids, gold):
    return next((n for n, sid in enumerate(ids, 1) if sid in gold), None)


async def freeze(cases_path, output):
    import torch
    torch.set_num_threads(4)
    cases = json.loads(private_path(cases_path).read_bytes())['cases']
    cfg = settings(local=True)
    index = MuseumIndex(cfg, MemoryStore())
    start = time.perf_counter()
    await index.start()
    print('Original MiniLM index ready', flush=True)
    report = dict(complete=False, model=cfg.museum_embedding_model, versions=versions(cfg),
                  cases_sha256=digest(cases_path), build_ms=round((time.perf_counter()-start)*1000), rows=[])
    with private_path(output).open('x', encoding='utf-8') as f:
        for c in cases:
            vector = index.embedding._local_embed([c['query']])[0]
            dense = await index.vectors.search(vector, top_k=25)
            start = time.perf_counter()
            hits, trace = await index.search_for_answer(c['query'])
            ids = [r['_id'] for r in hits]
            report['rows'].append(dict(**c, dense=dense, baseline_ids=ids,
                baseline_rank=rank(ids, c['gold']), baseline_trace=trace,
                baseline_ms=round((time.perf_counter()-start)*1000, 2)))
            f.seek(0); json.dump(report, f, ensure_ascii=False, indent=2); f.truncate(); f.flush()
        report['complete'] = True
        f.seek(0); json.dump(report, f, ensure_ascii=False, indent=2); f.truncate()
    await index.close()
    print(json.dumps(dict(frozen=len(cases), api_calls=0)), flush=True)


class FrozenEmbedding:
    def __init__(self, rows):
        self.positions = {c['query']:i for i, c in enumerate(rows)}

    def _local_embed(self, texts):
        return [[self.positions[text]] for text in texts]


class FrozenVectors:
    def __init__(self, rows):
        self.rows = rows

    async def search(self, vector, top_k=25, dept_id=None):
        if top_k > 25 or dept_id is not None:
            raise ValueError('Replay only supports the frozen retrieval bounds')
        return self.rows[int(vector[0])]['dense'][:top_k]


async def replay(frozen_path, output):
    frozen = json.loads(private_path(frozen_path).read_bytes())
    assert frozen['complete']
    cfg = settings()
    assert versions(cfg) == frozen['versions']
    assert cfg.museum_embedding_model == frozen['model']
    index = MuseumIndex(cfg, MemoryStore())
    await index.start()
    index.embedding = FrozenEmbedding(frozen['rows'])
    index.vectors = FrozenVectors(frozen['rows'])
    index.hybrid.vector_store = index.vectors
    report = dict(complete=False, frozen_sha256=digest(frozen_path), versions=versions(cfg),
                  scope='Fixed real MiniLM candidates; production lexical/fusion replay; no VLM or BGE', rows=[])
    for c in frozen['rows']:
        cfg.museum_catalogue_glossary = False
        before, _ = await index.search_for_answer(c['query'])
        before_ids = [r['_id'] for r in before]
        assert before_ids == c['baseline_ids'], ('Baseline drift', c['id'])
        cfg.museum_catalogue_glossary = True
        start = time.perf_counter()
        after, trace = await index.search_for_answer(c['query'])
        after_ids = [r['_id'] for r in after]
        report['rows'].append(dict(id=c['id'], group=c['group'], query=c['query'], gold=c['gold'],
            before_ids=before_ids, after_ids=after_ids, before_rank=c['baseline_rank'],
            after_rank=rank(after_ids,c['gold']), trace=trace,
            replay_ms=round((time.perf_counter()-start)*1000, 2)))
    report['summary'] = {}
    for group in sorted({r['group'] for r in report['rows']}):
        rows = [r for r in report['rows'] if r['group']==group and r['gold']]
        report['summary'][group] = dict(n=len(rows),
            before_top1=sum(r['before_rank']==1 for r in rows), after_top1=sum(r['after_rank']==1 for r in rows),
            before_top5=sum(r['before_rank'] is not None for r in rows),
            after_top5=sum(r['after_rank'] is not None for r in rows),
            before_mrr_at5=round(sum(1/r['before_rank'] if r['before_rank'] else 0 for r in rows)/len(rows),4) if rows else None,
            after_mrr_at5=round(sum(1/r['after_rank'] if r['after_rank'] else 0 for r in rows)/len(rows),4) if rows else None,
            lost_top5=[r['id'] for r in rows if r['before_rank'] is not None and r['after_rank'] is None],
            worse_rank=[r['id'] for r in rows if r['before_rank'] is not None and (r['after_rank'] or 99)>r['before_rank']])
    report['complete'] = True
    with private_path(output).open('x', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(json.dumps(report['summary'], ensure_ascii=False, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['freeze', 'replay'])
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if private_path(args.output).exists():
        raise FileExistsError('Preserve previous evidence; choose a new output')
    asyncio.run((freeze if args.mode=='freeze' else replay)(args.input, args.output))
