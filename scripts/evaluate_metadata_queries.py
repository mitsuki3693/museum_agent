"""Freeze metadata-focused queries, then compare real local retrieval entries.

No generated answers, API calls, online activation or source edits. Relevance
sets contain record-backed positives, not exhaustive semantic judgments.
"""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import statistics
import time

from app.museum.config import MuseumSettings
from app.museum.retrieval import MuseumIndex
from app.museum.text_encoder import MINILM
from app.storage.store import MemoryStore

ROOT = Path(__file__).resolve().parents[1]
QUERIES = ROOT / 'eval/private/metadata-queries-v1.json'
OUT = ROOT / 'eval/private/metadata-query-results-v1.json'
LABELS = {
    'author': {'Maker', '作者 Artist'},
    'material': {'Materials and techniques', 'materialsAndTechniques', '材质 Medium'},
    'date': {'Date', '年代 Date'},
}
# Date tests use explicit displayed strings, not guessed historical intervals.
FACETS = [
    ('author', ['monet'], '找莫奈的作品', 'Find works by Claude Monet'),
    ('author', ['bologna'], '找乔瓦尼·博洛尼亚的作品', 'Find works by Giovanni Bologna'),
    ('author', ['greek a'], '找希腊A工厂制作的陶器', "Find ceramics by the Greek A factory"),
    ('author', ['de roos'], '找De Roos工厂的作品', 'Find works by the De Roos factory'),
    ('material', ['marble'], '找大理石作品', 'Find objects made of marble'),
    ('material', ['oil on canvas'], '找布面油画', 'Find oil paintings on canvas'),
    ('material', ['tin-glazed', 'blue'], '找绘有蓝色图案的锡釉陶器', 'Find tin-glazed earthenware painted in blue'),
    ('material', ['gilding'], '找带镀金装饰的作品', 'Find objects decorated with gilding'),
    ('date', ['2009'], '找年代标注为2009的作品', 'Find works dated 2009'),
    ('date', ['1930'], '找年代标注为1930的作品', 'Find works dated 1930'),
    ('date', ['1750-1775'], '找年代标注为1750-1775的作品', 'Find works dated 1750-1775'),
    ('date', ['ca. 1695'], '找年代标注为约1695年的作品', 'Find works dated circa 1695'),
]
ANCHORS = [
    ('artic-16568', '《睡莲》', 'Water Lilies'),
    ('artic-6565', '《美国哥特式》', 'American Gothic'),
    ('va-o14761-samson', '《参孙击杀非利士人》', 'Samson Slaying a Philistine'),
]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def metadata_lines(record, category):
    return [line for line in record['content'].splitlines()
            if line.partition(':')[0].strip() in LABELS[category] and line.partition(':')[2].strip()]


def matches(line, category, needles):
    value = line.partition(':')[2].strip().lower()
    return value == needles[0] if category == 'date' else all(term in value for term in needles)


def save_new(path, data):
    with path.open('x', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def freeze():
    if QUERIES.exists():
        raise FileExistsError('Keep frozen queries unchanged')
    active = MuseumSettings()
    assert active.museum_private_corpus and active.museum_embedding == 'local'
    paths = dict(web_config=active.museum_private_corpus.resolve(),
                 scale300=ROOT / 'data/private/va-pilot-300-v1-corpus.json')
    public = active.museum_corpus.resolve()
    records = {name: json.loads(p.read_bytes()) + json.loads(public.read_bytes()) for name, p in paths.items()}
    cases = []
    for i, (category, needles, zh, en) in enumerate(FACETS, 1):
        evidence = {name: {r['_id']: [line for line in metadata_lines(r, category) if matches(line, category, needles)]
                           for r in rows if any(matches(line, category, needles) for line in metadata_lines(r, category))}
                    for name, rows in records.items()}
        for lang, query in [('zh', zh), ('en', en)]:
            cases.append(dict(id=f'M{i:02}-{lang}', kind='facet', category=category, language=lang,
                              query=query, evidence=evidence))
    for i, (sid, zh, en) in enumerate(ANCHORS, 1):
        for category, questions in [
            ('author', [f'{zh}是谁创作的？', f'Who made {en}?']),
            ('material', [f'{zh}使用了什么材料？', f'What material was used for {en}?']),
            ('date', [f'{zh}创作于什么时候？', f'When was {en} made?']),
        ]:
            evidence = {name: {r['_id']: metadata_lines(r, category) for r in rows if r['_id'] == sid}
                        for name, rows in records.items()}
            assert all(e.get(sid) for e in evidence.values())
            for lang, query in zip(['zh', 'en'], questions, strict=True):
                cases.append(dict(id=f'A{i}-{category}-{lang}', kind='named', category=category,
                                  language=lang, query=query, object_id=sid, evidence=evidence))
    assert len(cases) == 42
    baseline = ROOT / 'eval/private/semantic-chunks-v1.json'
    save_new(QUERIES, dict(frozen=True, human_reviewed=False,
        relevance='Confirmed metadata positives; other results may need manual judgment. Not exhaustive relevance.',
        corpora={name: dict(private_path=str(p), private_sha256=sha(p), public_path=str(public),
                           public_sha256=sha(public), count=len(records[name])) for name, p in paths.items()},
        baseline_sha256=sha(baseline), cases=cases))
    print(json.dumps(dict(frozen=42, corpora={n:len(r) for n,r in records.items()}, sha256=sha(QUERIES))))


def summary(rows, view):
    eligible = [r for r in rows if r['gold']]
    ranks = [r['results'][view]['rank'] for r in eligible]
    latencies = sorted(r['results'][view]['ms'] for r in eligible)
    return dict(n=len(eligible), no_gold=sum(not r['gold'] for r in rows),
                hit1=sum(r == 1 for r in ranks), hit5=sum(r is not None for r in ranks),
                p50_ms=statistics.median(latencies) if latencies else None)


def check_gate():
    """Replay the frozen real-entry results; any lost positive blocks activation."""
    report = json.loads(OUT.read_bytes())
    assert report['complete'] and report['query_sha256'] == sha(QUERIES)
    failures = {name: corpus['changes']['regressed'] for name, corpus in report['corpora'].items()
                if corpus['changes']['regressed']}
    print(json.dumps(dict(activation_allowed=not failures, regressions=failures)))
    if failures:
        raise SystemExit(1)


async def diagnose():
    """One-variable probe: restore ALL material field vectors, never gold-only."""
    import torch
    from app.museum.retrieval import FusionOrder
    from app.museum.semantic_chunks import dense_views
    from app.retrieval.hybrid import HybridRetriever
    from app.retrieval.vector_store import MemoryVectorStore
    from evaluate_bm25f import stage_ranks
    torch.set_num_threads(4)
    out=ROOT/'eval/private/metadata-query-diagnosis-v1.json'
    if out.exists():raise FileExistsError('Preserve diagnosis')
    frozen=json.loads(QUERIES.read_bytes())
    results=json.loads(OUT.read_bytes())
    assert results['complete'] and results['query_sha256']==sha(QUERIES)
    report=dict(query_sha256=sha(QUERIES),results_sha256=sha(OUT),rows=[])
    for name, corpus in frozen['corpora'].items():
        private,public=Path(corpus['private_path']),Path(corpus['public_path'])
        assert sha(private)==corpus['private_sha256'] and sha(public)==corpus['public_sha256']
        cfg=MuseumSettings(_env_file=None,museum_corpus=public,museum_private_corpus=private,
                           museum_embedding='local',museum_dense_view='original',deepseek_api_key='')
        idx=MuseumIndex(cfg,MemoryStore());await idx.start()
        views=dense_views(idx.records.values())
        filtered_ids={c['_id'] for c in views['filtered']}
        restore_ids={a['chunk_id'] for a in views['audit'] if a['action']=='metadata_dense_excluded'
                     and a['label'] in LABELS['material']}
        stores={'original':idx.vectors}
        for view,allowed in [('filtered',filtered_ids),('restore_material',filtered_ids|restore_ids)]:
            store=MemoryVectorStore()
            for cid,meta in idx.vectors._meta.items():
                if cid in allowed:await store.add(cid,idx.vectors._vecs[cid],meta)
            stores[view]=store
        for case in [c for c in frozen['cases'] if c['id'] in ['M06-zh','A3-material-zh']]:
            gold=list(case['evidence'][name]);vector=idx.embedding._local_embed([case['query']])[0]
            lexical=idx.bm25.search(case['query'],top_k=25)
            row=dict(corpus=name,id=case['id'],query=case['query'],gold=gold,
                     lexical=stage_ranks(lexical,gold),arms={})
            for view,store in stores.items():
                retriever=HybridRetriever(idx.bm25,store,FusionOrder(),bm25_top=25,vector_top=25,top_k=25)
                dense=await store.search(vector,top_k=len(store._meta))
                fused=await retriever.retrieve(case['query'],vector)
                ids=[r['_id'] for r in (await idx._current_sources([h['source_id'] for h in fused]))[:5]]
                row['arms'][view]=dict(chunks=len(store._meta),dense=stage_ranks(dense,gold),
                                      ids=ids,hit=bool(set(ids)&set(gold)))
                if view in ['original','filtered']:
                    prior=next(r for r in results['corpora'][name]['rows'] if r['id']==case['id'])
                    assert ids==prior['results'][view]['ids'], 'Probe failed to reproduce'
            report['rows'].append(row)
            print(json.dumps(row,ensure_ascii=False),flush=True)
    save_new(out,report)


async def evaluate():
    import torch
    torch.set_num_threads(4)
    if OUT.exists():
        raise FileExistsError('Preserve earlier run')
    queries = json.loads(QUERIES.read_bytes())
    assert queries['frozen']
    basepath = ROOT / 'eval/private/semantic-chunks-v1.json'
    assert sha(basepath) == queries['baseline_sha256']
    baseline = json.loads(basepath.read_bytes())
    original = json.loads((ROOT/'eval/private/text-encoders-v1.json').read_bytes())
    modelroot = ROOT/'models/models--sentence-transformers--paraphrase-multilingual-MiniLM-L12-v2'
    assert (modelroot/'refs/main').read_text().strip() == MINILM.revision
    for name, expected in original['versions']['minilm_files'].items():
        assert sha(modelroot/'snapshots'/MINILM.revision/name) == expected
    report = dict(complete=False, query_sha256=sha(QUERIES), baseline_sha256=sha(basepath),
                  model_revision=MINILM.revision, code_sha256=sha(Path(__file__)), corpora={})
    for name, corpus in queries['corpora'].items():
        private, public = Path(corpus['private_path']), Path(corpus['public_path'])
        assert sha(private) == corpus['private_sha256'] and sha(public) == corpus['public_sha256']
        indices = {}
        for view in ['original', 'filtered']:
            cfg = MuseumSettings(_env_file=None, museum_corpus=public, museum_private_corpus=private,
                                 museum_embedding='local', museum_dense_view=view, deepseek_api_key='')
            index = MuseumIndex(cfg, MemoryStore())
            await index.start()
            assert len(index.records) == corpus['count']
            indices[view] = index
        rows = []
        cases = [dict(c, gold=list(c['evidence'][name])) for c in queries['cases']]
        # Re-run frozen older queries only when ALL intended gold is present.
        cases += [dict(id='old-'+r['id'], query=r['query'], gold=r['gold'], kind='old', category='old', language='mixed')
                  for r in baseline['rows'] if r['gold'] and set(r['gold']) <= indices['original'].records.keys()]
        for n, case in enumerate(cases):
            row = {k:case[k] for k in ['id','query','gold','kind','category','language']}
            row['results'] = {}
            for view in (['original','filtered'] if n%2 else ['filtered','original']):
                t=time.perf_counter()
                hits=await indices[view].search(case['query'])
                ids=[r['_id'] for r in hits]
                row['results'][view]=dict(ids=ids, ms=(time.perf_counter()-t)*1000,
                    rank=next((i for i,sid in enumerate(ids,1) if sid in case['gold']),None))
                if case['kind']=='named':
                    scoped=await indices[view].search(case['query'],object_id=case['object_id'])
                    assert scoped and scoped[0]['_id']==case['object_id']
                    assert indices[view].records[case['object_id']]['content']==scoped[0]['content']
            if name=='scale300' and case['kind']=='old':
                previous=next(r for r in baseline['rows'] if 'old-'+r['id']==case['id'])
                for view in indices:
                    assert row['results'][view]['ids']==previous['results']['minilm_'+view]['ids'], 'Baseline drift'
            rows.append(row)
        sections={}
        for section in ['facet','named','old','author','material','date']:
            selected=[r for r in rows if r['kind']==section or r['category']==section]
            sections[section]={view:summary(selected,view) for view in indices}
        def hit(r, view):return r['results'][view]['rank'] is not None
        changes=dict(fixed=[r['id'] for r in rows if r['gold'] and not hit(r,'original') and hit(r,'filtered')],
                     regressed=[r['id'] for r in rows if r['gold'] and hit(r,'original') and not hit(r,'filtered')])
        report['corpora'][name]=dict(source_hashes=corpus, rows=rows, summary=sections, changes=changes)
        print(json.dumps(dict(corpus=name, summary=sections, changes=changes)), flush=True)
    report['complete']=True
    save_new(OUT,report)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--freeze',action='store_true')
    parser.add_argument('--check',action='store_true')
    parser.add_argument('--diagnose',action='store_true')
    args=parser.parse_args()
    if args.diagnose:asyncio.run(diagnose())
    elif args.check:check_gate()
    elif args.freeze:freeze()
    else:asyncio.run(evaluate())
