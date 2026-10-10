"""Private local JSON-line protocol. Never loads .env or uses a model API."""
import contextlib
import hashlib
import json
from pathlib import Path
import sys
import time
import argparse

from .local_reranker import LocalPairReranker,MODEL_REVISION,contextual_evidence,ranked_ids
from .semantic_chunks import dense_views
from .evidence_ties import POLICY, stable_evidence_ranking
from .rerank_evidence_controls import remove_repeated_title,prioritize_material,VERSION as CONTROLS_VERSION


def score_request(model, request, *, sort_by_length=False,evidence_controls=False,context_budget=256):
    started=time.perf_counter();evidence_tokenize_ms=0.0
    if context_budget not in (160,192,256):raise ValueError('Invalid evidence budget')
    query=request['query'];candidates=request['candidates'];sources=request['sources']
    if not isinstance(query,str) or not 0<len(query)<=600 or not 1<=len(candidates)<=25:
        raise ValueError('Invalid request bounds')
    records={s['_id']:s for s in sources}
    if len(records)!=len(sources) or set(records)!={c['source_id'] for c in candidates}:
        raise ValueError('Source identity mismatch')
    passages=[];audit=[]
    for candidate in candidates:
        source=records[candidate['source_id']]
        if source.get('status')!='active':raise ValueError('Inactive source')
        def fits(text):
            nonlocal evidence_tokenize_ms
            tick=time.perf_counter()
            size=len(model.tokenizer(query,text,truncation=False)['input_ids'])
            evidence_tokenize_ms+=(time.perf_counter()-tick)*1000
            return size<=context_budget
        evidence=contextual_evidence(source,candidate,dense_views([source])['original'],fits=fits)
        passage=remove_repeated_title(evidence['passage']) if evidence_controls else evidence['passage']
        passages.append(passage)
        audit.append(dict(source_id=source['_id'],source_hash=source['source_hash'],
            passage_sha256=hashlib.sha256(passage.encode()).hexdigest(),
            **{k:[{a:v for a,v in item.items() if a!='text'} for item in evidence[k]] for k in ['included','omitted']}))
    evidence_ms=(time.perf_counter()-started)*1000
    result=model.score(query,passages,sort_by_length=True) if sort_by_length else model.score(query,passages)
    if result['truncated'] or max(result['token_lengths'])>context_budget:raise ValueError('Evidence budget drift')
    ids=ranked_ids(candidates,result['scores']);ties=[]
    if sort_by_length or evidence_controls:ids,ties=stable_evidence_ranking(candidates,result['scores'],passages)
    material={}
    if evidence_controls:ids,material=prioritize_material(query,ids,records)
    profile=dict(evidence_ms=round(evidence_ms,3),evidence_tokenize_ms=round(evidence_tokenize_ms,3),
                 evidence_assembly_ms=round(max(0,evidence_ms-evidence_tokenize_ms),3),
                 **getattr(model,'last_profile',{}),worker_ms=round((time.perf_counter()-started)*1000,3))
    return dict(ids=ids,evidence=audit,model_revision=MODEL_REVISION,profile=profile,context_token_budget=context_budget,
                batching='length' if sort_by_length else 'original',
                ranking_policy=POLICY if sort_by_length or evidence_controls else 'raw-score-v1',evidence_ties=ties,
                evidence_controls=CONTROLS_VERSION if evidence_controls else None,material_status=material.get('status'))


def emit(value):
    sys.stdout.write(json.dumps(value)+'\n');sys.stdout.flush()


def main():
    started=time.perf_counter()
    parser=argparse.ArgumentParser()
    parser.add_argument('model_path',type=Path)
    parser.add_argument('--sort-by-length',action='store_true')
    parser.add_argument('--evidence-controls',action='store_true')
    parser.add_argument('--context-budget',type=int,choices=[160,192,256],default=256)
    parser.add_argument('--backend',choices=['torch','onnx','int8'],default='torch')
    parser.add_argument('--onnx-path',type=Path)
    args=parser.parse_args()
    try:
        with contextlib.redirect_stdout(sys.stderr):
            path=args.model_path;sort_by_length=args.sort_by_length;controls=args.evidence_controls
            manifest=json.loads((path/'download-manifest.json').read_bytes())
            if manifest['revision']!=MODEL_REVISION:raise ValueError('Model revision mismatch')
            for name,meta in manifest['files'].items():
                file=(path/name).resolve()
                if not file.is_relative_to(path.resolve()):raise ValueError('Invalid model path')
                with file.open('rb') as handle:digest=hashlib.file_digest(handle,'sha256').hexdigest()
                if digest!=meta['sha256']:raise ValueError('Model checksum mismatch')
            if args.backend!='torch':
                if args.onnx_path is None:raise ValueError('Missing ONNX export')
                export=json.loads((args.onnx_path/'export-manifest.json').read_bytes())
                if export['revision']!=MODEL_REVISION:raise ValueError('Export revision mismatch')
                if export['source_manifest_sha256']!=hashlib.sha256((path/'download-manifest.json').read_bytes()).hexdigest():
                    raise ValueError('Export source mismatch')
                required='model-int8.onnx' if args.backend=='int8' else 'model.onnx'
                if required not in export['files']:raise ValueError('Missing graph digest')
                for name,meta in export['files'].items():
                    file=(args.onnx_path/name).resolve()
                    if not file.is_relative_to(args.onnx_path.resolve()):raise ValueError('Invalid export path')
                    with file.open('rb') as handle:digest=hashlib.file_digest(handle,'sha256').hexdigest()
                    if digest!=meta['sha256']:raise ValueError('Export checksum mismatch')
            checksum_ms=(time.perf_counter()-started)*1000
            import torch
            torch.set_num_threads(4)
            tick=time.perf_counter()
            if args.backend=='torch':model=LocalPairReranker(path,max_length=512,batch_size=4)
            else:
                from .onnx_reranker import OnnxPairReranker
                model=OnnxPairReranker(path,args.onnx_path/required,max_length=512,batch_size=4)
        emit(dict(ready=True,startup_profile=dict(checksum_ms=round(checksum_ms,3),
            model_load_ms=round((time.perf_counter()-tick)*1000,3),total_ms=round((time.perf_counter()-started)*1000,3))))
    except Exception as exc:
        emit(dict(ready=False,error=type(exc).__name__));return
    for line in sys.stdin:
        request={}
        try:
            request=json.loads(line)
            with contextlib.redirect_stdout(sys.stderr):result=score_request(model,request,sort_by_length=sort_by_length,evidence_controls=controls,context_budget=args.context_budget)
            emit(dict(request_id=request['request_id'],backend=args.backend,**result))
        except Exception as exc:
            emit(dict(request_id=request.get('request_id'),error=type(exc).__name__))


if __name__=='__main__':main()
