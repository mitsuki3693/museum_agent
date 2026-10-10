"""Offline cross-encoder experiment; not used by the application search path."""
import math
import time
from .semantic_chunks import METADATA_LABELS

MODEL_REVISION='953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e'
PASSAGE_VERSION='metadata-plus-retrieved-evidence-v1'
ATTRIBUTE_LABELS=frozenset({'Maker','Date','Place','Materials and techniques','materialsAndTechniques',
    '作者 Artist','年代 Date','材质 Medium','创作地 Origin','分类 Classification'})

CONTEXT_PASSAGE_VERSION='metadata-retrieved-neighbours-summary-v2'


def contextual_evidence(source, candidate, chunks, *, fits):
    """Pack source excerpts under a caller's token budget; never consult gold.

    Priority is attributes, lane winners, brief description, two neighbours on
    either side of each winner, then the first two summary chunks. Whole chunks
    only; omitted excerpts are recorded rather than silently truncated.
    """
    if candidate['source_id'] != source['_id']:
        raise ValueError('Candidate/source mismatch')
    if any(c['source_id'] != source['_id'] for c in chunks):
        raise ValueError('Cross-source context')
    positions={c['_id']:i for i,c in enumerate(chunks)}
    if len(positions)!=len(chunks):raise ValueError('Duplicate context chunk')
    prefix=source['title']+'\n'
    def body(chunk):
        if not chunk['content'].startswith(prefix):raise ValueError('Invalid chunk title')
        return chunk['content'][len(prefix):]
    bodies=[body(c) for c in chunks]
    proposals=[dict(text=source['title'],reason='title',chunk_id=None)]
    def add(i,reason):
        proposals.append(dict(text=bodies[i],reason=reason,chunk_id=chunks[i]['_id']))
    for i,text in enumerate(bodies):
        label,sep,value=text.partition(':')
        if sep and label.strip() in ATTRIBUTE_LABELS and value.strip():add(i,'attribute')
    winners=[]
    for lane in ['lexical','dense']:
        evidence=candidate.get('lanes',{}).get(lane)
        if not evidence:continue
        i=positions[evidence['best_chunk_id']]
        if chunks[i]['content']!=evidence['best_content']:raise ValueError('Changed retrieval evidence')
        winners.append(i);add(i,'retrieved_'+lane)
    for i,text in enumerate(bodies):
        if text.startswith('briefDescription:'):add(i,'brief')
    # Earlier context first, in source order (sentences can span two chunks).
    for offset in [-2,-1,1,2]:
        for i in winners:
            j=i+offset
            if 0<=j<len(chunks):
                label=bodies[j].partition(':')[0].strip()
                if label not in METADATA_LABELS:add(j,'neighbour')
    for i,text in enumerate(bodies):
        if text.startswith('summaryDescription:') or text.startswith('馆方介绍 Description:'):
            add(i,'summary')
            if i+1<len(chunks) and ':' not in bodies[i+1]:add(i+1,'summary_continuation')
    included=[];omitted=[];seen=set();parts=[]
    for item in proposals:
        if item['text'] in seen:continue
        seen.add(item['text'])
        if fits('\n'.join(parts+[item['text']])):
            parts.append(item['text']);included.append(item)
        else:omitted.append(item)
    if not parts or included[0]['reason']!='title':raise ValueError('Budget cannot fit title')
    return dict(passage='\n'.join(parts),included=included,omitted=omitted)


def evidence_passage(source, candidate):
    """Use unmodified descriptive fields and only already-retrieved evidence."""
    if candidate['source_id']!=source['_id']:
        raise ValueError('Candidate/source mismatch')
    parts=[source['title']]
    parts.extend(line for line in source['content'].splitlines() if line.partition(':')[0].strip() in ATTRIBUTE_LABELS)
    for lane in ['lexical','dense']:
        evidence=candidate.get('lanes',{}).get(lane)
        if evidence and evidence.get('best_content'):
            text=evidence['best_content']
            prefix=source['title']+'\n'
            parts.append(text[len(prefix):] if text.startswith(prefix) else text)
    return '\n'.join(dict.fromkeys(parts))


def ranked_ids(candidates, scores):
    if len(candidates)!=len(scores) or not all(math.isfinite(float(s)) for s in scores):
        raise ValueError('Invalid reranker scores')
    ids=[c['source_id'] for c in candidates]
    if len(set(ids))!=len(ids):raise ValueError('Duplicate candidate identity')
    return [ids[i] for i in sorted(range(len(ids)),key=lambda i:float(scores[i]),reverse=True)]


def batch_indices(lengths, batch_size, *, sort_by_length=False):
    """Stable optional grouping; callers must restore original result indices."""
    if batch_size < 1:
        raise ValueError('batch_size must be positive')
    order = sorted(range(len(lengths)), key=lambda i:lengths[i]) if sort_by_length else list(range(len(lengths)))
    return [order[start:start+batch_size] for start in range(0,len(order),batch_size)]


class LocalPairReranker:
    def __init__(self,path,*,max_length=512,batch_size=4):
        import torch
        from transformers import AutoModelForSequenceClassification,AutoTokenizer
        self.torch=torch;self.max_length=max_length;self.batch_size=batch_size
        self.tokenizer=AutoTokenizer.from_pretrained(str(path),local_files_only=True,trust_remote_code=False)
        self.model=AutoModelForSequenceClassification.from_pretrained(str(path),local_files_only=True,
            trust_remote_code=False,use_safetensors=True,dtype=torch.float32).to('cpu').eval()
        if self.model.config.num_labels!=1:raise ValueError('Expected one relevance logit')

    def score(self,query,passages,*,sort_by_length=False):
        started=time.perf_counter()
        self.last_profile=dict(tokenize_ms=0.0,inference_ms=0.0,batches=[])
        pairs=[[query,p] for p in passages]
        if not pairs:return dict(scores=[],token_lengths=[],truncated=0)
        tick=time.perf_counter()
        lengths=[len(t) for t in self.tokenizer(pairs,truncation=False)['input_ids']]
        self.last_profile['tokenize_ms']+=(time.perf_counter()-tick)*1000
        scores=[None]*len(pairs)
        with self.torch.inference_mode():
            for indices in batch_indices([min(n,self.max_length) for n in lengths],self.batch_size,sort_by_length=sort_by_length):
                tick=time.perf_counter()
                inputs=self.tokenizer([pairs[i] for i in indices],padding=True,truncation='only_second',
                    return_tensors='pt',max_length=self.max_length)
                token_ms=(time.perf_counter()-tick)*1000
                self.last_profile['tokenize_ms']+=token_ms
                tick=time.perf_counter()
                values=self.model(**inputs,return_dict=True).logits.reshape(-1).float().tolist()
                infer_ms=(time.perf_counter()-tick)*1000
                self.last_profile['inference_ms']+=infer_ms
                self.last_profile['batches'].append(dict(size=len(indices),max_tokens=max(lengths[i] for i in indices),
                    tokenize_ms=round(token_ms,3),inference_ms=round(infer_ms,3)))
                for index,value in zip(indices,values,strict=True):scores[index]=value
        if len(scores)!=len(pairs) or not all(math.isfinite(s) for s in scores):
            raise ValueError('Non-finite or missing model output')
        self.last_profile['score_ms']=(time.perf_counter()-started)*1000
        return dict(scores=scores,token_lengths=lengths,truncated=sum(n>self.max_length for n in lengths))
