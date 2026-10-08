"""Private local JSON-line protocol. Never loads .env or uses a model API."""
import contextlib
import hashlib
import json
from pathlib import Path
import sys

from .local_reranker import LocalPairReranker,MODEL_REVISION,contextual_evidence,ranked_ids
from .semantic_chunks import dense_views


def score_request(model, request, *, sort_by_length=False):
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
        def fits(text):return len(model.tokenizer(query,text,truncation=False)['input_ids'])<=256
        evidence=contextual_evidence(source,candidate,dense_views([source])['original'],fits=fits)
        passages.append(evidence['passage'])
        audit.append(dict(source_id=source['_id'],source_hash=source['source_hash'],
            passage_sha256=hashlib.sha256(evidence['passage'].encode()).hexdigest(),
            **{k:[{a:v for a,v in item.items() if a!='text'} for item in evidence[k]] for k in ['included','omitted']}))
    result=model.score(query,passages,sort_by_length=True) if sort_by_length else model.score(query,passages)
    if result['truncated'] or max(result['token_lengths'])>256:raise ValueError('Evidence budget drift')
    return dict(ids=ranked_ids(candidates,result['scores']),evidence=audit,model_revision=MODEL_REVISION,
                batching='length' if sort_by_length else 'original')


def emit(value):
    sys.stdout.write(json.dumps(value)+'\n');sys.stdout.flush()


def main():
    try:
        with contextlib.redirect_stdout(sys.stderr):
            path=Path(sys.argv[1]);sort_by_length='--sort-by-length' in sys.argv[2:]
            manifest=json.loads((path/'download-manifest.json').read_bytes())
            if manifest['revision']!=MODEL_REVISION:raise ValueError('Model revision mismatch')
            for name,meta in manifest['files'].items():
                file=(path/name).resolve()
                if not file.is_relative_to(path.resolve()):raise ValueError('Invalid model path')
                with file.open('rb') as handle:digest=hashlib.file_digest(handle,'sha256').hexdigest()
                if digest!=meta['sha256']:raise ValueError('Model checksum mismatch')
            import torch
            torch.set_num_threads(4)
            model=LocalPairReranker(path,max_length=512,batch_size=4)
        emit(dict(ready=True))
    except Exception as exc:
        emit(dict(ready=False,error=type(exc).__name__));return
    for line in sys.stdin:
        request={}
        try:
            request=json.loads(line)
            with contextlib.redirect_stdout(sys.stderr):result=score_request(model,request,sort_by_length=sort_by_length)
            emit(dict(request_id=request['request_id'],**result))
        except Exception as exc:
            emit(dict(request_id=request.get('request_id'),error=type(exc).__name__))


if __name__=='__main__':main()
