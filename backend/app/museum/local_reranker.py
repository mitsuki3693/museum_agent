"""Offline cross-encoder experiment; not used by the application search path."""
import math

MODEL_REVISION='953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e'
PASSAGE_VERSION='metadata-plus-retrieved-evidence-v1'
ATTRIBUTE_LABELS=frozenset({'Maker','Date','Place','Materials and techniques','materialsAndTechniques',
    '作者 Artist','年代 Date','材质 Medium','创作地 Origin','分类 Classification'})


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


class LocalPairReranker:
    def __init__(self,path,*,max_length=512,batch_size=4):
        import torch
        from transformers import AutoModelForSequenceClassification,AutoTokenizer
        self.torch=torch;self.max_length=max_length;self.batch_size=batch_size
        self.tokenizer=AutoTokenizer.from_pretrained(str(path),local_files_only=True,trust_remote_code=False)
        self.model=AutoModelForSequenceClassification.from_pretrained(str(path),local_files_only=True,
            trust_remote_code=False,use_safetensors=True,dtype=torch.float32).to('cpu').eval()
        if self.model.config.num_labels!=1:raise ValueError('Expected one relevance logit')

    def score(self,query,passages):
        pairs=[[query,p] for p in passages]
        if not pairs:return dict(scores=[],token_lengths=[],truncated=0)
        lengths=[len(t) for t in self.tokenizer(pairs,truncation=False)['input_ids']]
        scores=[]
        with self.torch.inference_mode():
            for start in range(0,len(pairs),self.batch_size):
                inputs=self.tokenizer(pairs[start:start+self.batch_size],padding=True,truncation='only_second',
                    return_tensors='pt',max_length=self.max_length)
                values=self.model(**inputs,return_dict=True).logits.reshape(-1).float().tolist()
                scores.extend(values)
        if len(scores)!=len(pairs) or not all(math.isfinite(s) for s in scores):
            raise ValueError('Non-finite or missing model output')
        return dict(scores=scores,token_lengths=lengths,truncated=sum(n>self.max_length for n in lengths))
