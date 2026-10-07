"""Freeze administrative dense input and compare chunk/work equal-weight RRF."""
import asyncio
import json
from pathlib import Path
import time

from app.museum.config import MuseumSettings
from app.museum.retrieval import MuseumIndex
from app.museum.text_encoder import MINILM
from app.museum.work_fusion import fuse_works, VERSION
from app.storage.store import MemoryStore
from evaluate_metadata_queries import sha, save_new, summary
from evaluate_bm25f import stage_ranks

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'eval/private/work-fusion-v1.json'


async def main():
    import torch
    torch.set_num_threads(4)
    if OUT.exists():raise FileExistsError('Preserve earlier experiment')
    previous_path=ROOT/'eval/private/administrative-filter-v1.json'
    previous=json.loads(previous_path.read_bytes());assert previous['complete']
    assert sha(ROOT/'eval/private/metadata-queries-v1.json')==previous['query_sha256']
    model_baseline=json.loads((ROOT/'eval/private/text-encoders-v1.json').read_bytes())
    modelroot=ROOT/'models/models--sentence-transformers--paraphrase-multilingual-MiniLM-L12-v2'
    assert (modelroot/'refs/main').read_text().strip()==MINILM.revision
    for name,expected in model_baseline['versions']['minilm_files'].items():
        assert sha(modelroot/'snapshots'/MINILM.revision/name)==expected
    report=dict(complete=False,baseline_sha256=sha(previous_path),query_sha256=previous['query_sha256'],
        model_revision=MINILM.revision,version=VERSION,
        fixed=dict(dense_view='administrative',bm25_top=25,vector_top=25,rrf_k=60,weights=[1,1],final_works=5),
        code_sha256={p:sha(ROOT/p) for p in ['scripts/evaluate_work_fusion.py','backend/app/museum/work_fusion.py',
            'backend/app/museum/retrieval.py','backend/app/retrieval/hybrid.py','backend/app/museum/semantic_chunks.py']},corpora={})
    for name,corpus in previous['corpora'].items():
        sources=corpus['source_hashes'];private,public=Path(sources['private_path']),Path(sources['public_path'])
        assert sha(private)==sources['private_sha256'] and sha(public)==sources['public_sha256']
        index=MuseumIndex(MuseumSettings(_env_file=None,museum_corpus=public,museum_private_corpus=private,
            museum_embedding='local',museum_dense_view='administrative',deepseek_api_key=''),MemoryStore())
        await index.start()
        rows=[]
        for n,case in enumerate(corpus['rows']):
            row={k:case[k] for k in ['id','query','gold','kind','category','language']};row['results']={}
            start=time.perf_counter();vector=index.embedding._local_embed([case['query']])[0]
            encode_ms=(time.perf_counter()-start)*1000
            for arm in (['chunk','work'] if n%2 else ['work','chunk']):
                start=time.perf_counter()
                if arm=='chunk':hits=await index.hybrid.retrieve(case['query'],vector)
                else:
                    lexical=index.bm25.search(case['query'],top_k=25)
                    dense=await index.vectors.search(vector,top_k=25)
                    hits=fuse_works(lexical,dense)
                ids=[s['_id'] for s in (await index._current_sources([h['source_id'] for h in hits]))[:5]]
                ms=encode_ms+(time.perf_counter()-start)*1000
                row['results'][arm]=dict(ids=ids,rank=next((i for i,s in enumerate(ids,1) if s in case['gold']),None),ms=ms)
                if arm=='chunk':assert ids==case['results']['administrative']['ids'],(name,case['id'],'baseline drift')
                else:row['work_evidence']=hits
            row['coarse']=dict(lexical=stage_ranks(lexical,case['gold']),dense=stage_ranks(dense,case['gold']),
                              union_works=list(dict.fromkeys(h['source_id'] for h in lexical+dense)))
            rows.append(row)
        sections={s:{arm:summary([r for r in rows if r['kind']==s or r['category']==s],arm) for arm in ['chunk','work']}
                  for s in ['facet','named','old','author','material','date']}
        changes={}
        for cutoff in [1,5]:
            def ok(r,arm):
                rank=r['results'][arm]['rank'];return rank is not None and rank<=cutoff
            changes[f'top{cutoff}']=dict(fixed=[r['id'] for r in rows if r['gold'] and not ok(r,'chunk') and ok(r,'work')],
                                       regressed=[r['id'] for r in rows if r['gold'] and ok(r,'chunk') and not ok(r,'work')])
        report['corpora'][name]=dict(source_hashes=sources,rows=rows,summary=sections,changes=changes,
                                    no_new_top5_regressions=not changes['top5']['regressed'])
        print(json.dumps(dict(corpus=name,summary=sections,changes=changes)),flush=True)
    report['complete']=True
    save_new(OUT,report)


if __name__=='__main__':asyncio.run(main())
