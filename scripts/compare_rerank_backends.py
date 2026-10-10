"""Frozen source/candidate benchmark; emits private evidence, never edits gold."""
import argparse
import hashlib
import json
from pathlib import Path
import time

from app.museum.config import ROOT
from app.museum.local_reranker import LocalPairReranker
from app.museum.onnx_reranker import OnnxPairReranker
from app.museum.rerank_worker import score_request
from benchmark_rerank_latency import IDS
from evaluate_bilingual_retrieval import private_path,rank


def main(args):
    import psutil
    import torch
    torch.set_num_threads(4)
    path=ROOT/'eval/private/rerank-latency-input-v1.json'
    data=json.loads(path.read_bytes());assert data['complete']
    rows=[r for r in data['rows'] if r['request'] and (args.scope=='full' or r['id'] in IDS)]
    start=time.perf_counter()
    model_path=ROOT/'models/bge-reranker-v2-m3'
    if args.backend=='torch':model=LocalPairReranker(model_path)
    else:model=OnnxPairReranker(model_path,ROOT/'models/bge-reranker-v2-m3-onnx-v1'/('model-int8.onnx' if args.backend=='int8' else 'model.onnx'))
    report=dict(complete=False,input_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),backend=args.backend,
        budget=args.budget,scope=args.scope,rounds=args.rounds,load_ms=round((time.perf_counter()-start)*1000),rows=[])
    cached={}
    if args.reuse_service_profile:
        prior=json.loads(args.reuse_service_profile.read_bytes())
        assert args.backend=='torch' and args.budget==256 and args.rounds==1
        assert prior['complete'] and prior['input_sha256']==report['input_sha256']
        cached={r['id']:r for r in prior['rows'] if r['round']==1}
        report['reused_from_sha256']=hashlib.sha256(args.reuse_service_profile.read_bytes()).hexdigest()
    with private_path(args.output).open('x',encoding='utf-8') as out:
        # A separate warm-up, excluded from steady-state latency.
        warm=score_request(model,rows[0]['request'],sort_by_length=True,evidence_controls=True,context_budget=args.budget)
        report['warmup']=warm['profile']
        for repetition in range(args.rounds):
            for row in rows:
                if row['id'] in cached:
                    old=cached[row['id']]
                    result={k:old['trace'][k] for k in ['evidence','model_revision','profile','context_token_budget']}
                    result.update(ids=old['ids'],reused=True)
                else:
                    result=score_request(model,row['request'],sort_by_length=True,evidence_controls=True,context_budget=args.budget)
                report['rows'].append(dict(id=row['id'],round=repetition+1,rank=rank(result['ids'][:5],row['gold']),
                    rss_mb=round(psutil.Process().memory_info().rss/2**20,1),**result))
                out.seek(0);json.dump(report,out,ensure_ascii=False,indent=2);out.truncate();out.flush()
                print(json.dumps(dict(done=len(report['rows']),total=len(rows)*args.rounds,id=row['id'],
                    ms=round(result['profile']['worker_ms']),rank=report['rows'][-1]['rank'])),flush=True)
        report['complete']=True
        out.seek(0);json.dump(report,out,ensure_ascii=False,indent=2);out.truncate()


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--backend',choices=['torch','onnx','int8'],required=True)
    p.add_argument('--budget',type=int,choices=[160,192,256],default=256)
    p.add_argument('--scope',choices=['probe','full'],default='probe')
    p.add_argument('--rounds',type=int,default=1)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--reuse-service-profile',type=Path)
    args=p.parse_args()
    if private_path(args.output).exists():raise FileExistsError('Preserve results')
    main(args)
