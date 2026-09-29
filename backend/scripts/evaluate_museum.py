"""Paired offline retrieval comparison; optional live answers require reviewed gold data."""
import argparse
import asyncio
import hashlib
import json
import platform
import time
from pathlib import Path
from app.museum.config import MuseumSettings, ROOT
from app.museum.retrieval import MuseumIndex
from app.museum.engine import MuseumEngine
from app.storage.store import MemoryStore

async def run(args):
    settings=MuseumSettings()
    raw=(ROOT/'eval/questions.json').read_bytes()
    cases=json.loads(raw)
    if args.answers and (not settings.deepseek_api_key or any(not c['reviewed'] for c in cases)):
        raise SystemExit('Live answer evaluation requires a configured key and human-reviewed questions. No paid calls made.')
    store=MemoryStore()
    index=MuseumIndex(settings,store)
    await index.start()
    outputs=[]
    for variant in ['bm25','hybrid']:
        for case in cases:
            if not case['gold_source_ids'] or case.get('history'):
                continue
            start=time.perf_counter()
            # No selected-object shortcut: measure actual corpus retrieval.
            hits=await index.search(case['question'],None,variant)
            ids=[h['_id'] for h in hits]
            ranks=[ids.index(g)+1 for g in case['gold_source_ids'] if g in ids]
            row={'id':case['id'],'variant':variant,'query':case['question'],'gold_source_ids':case['gold_source_ids'],
                 'retrieved_ids':ids,'recall_at_5':len(ranks)/len(case['gold_source_ids']),
                 'reciprocal_rank':1/min(ranks) if ranks else 0,'latency_ms':round((time.perf_counter()-start)*1000)}
            if args.answers:
                result=await MuseumEngine(settings,store,index).answer(case['question'],{'_id':case['id']+variant},'brief',None,variant)
                row['answer_result']=result
                row['human_answer_grade']=None
            outputs.append(row)
    summary={}
    for variant in ['bm25','hybrid']:
        rows=[r for r in outputs if r['variant']==variant]
        summary[variant]={'cases':len(rows),'recall_at_5':sum(r['recall_at_5'] for r in rows)/max(len(rows),1),
                          'mrr':sum(r['reciprocal_rank'] for r in rows)/max(len(rows),1)}
    report={'kind':'offline paired retrieval comparison','gold_reviewed':all(c['reviewed'] for c in cases),
        'not_online_ab_test':True,'not_answer_accuracy':True,'corpus_sha256':index.corpus_hash,
        'questions_sha256':hashlib.sha256(raw).hexdigest(),'embedding':settings.museum_embedding,
        'embedding_model':settings.museum_embedding_model,'prompt_version':MuseumEngine.PROMPT_VERSION,
        'python':platform.python_version(),'generated_at':time.time(),'summary':summary,'results':outputs}
    path=ROOT/'eval/retrieval-smoke.json'
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'report':str(path),'gold_reviewed':report['gold_reviewed'],'summary':summary},ensure_ascii=False,indent=2))

if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--answers',action='store_true')
    asyncio.run(run(p.parse_args()))
