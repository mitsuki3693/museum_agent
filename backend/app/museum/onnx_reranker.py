"""Optional local CPU backend; same tokenizer, batches and unnormalized logit."""
import math
import time

from .local_reranker import batch_indices


class OnnxPairReranker:
    def __init__(self,tokenizer_path,model_path,*,max_length=512,batch_size=4):
        import onnxruntime as ort
        from transformers import AutoTokenizer
        options=ort.SessionOptions();options.intra_op_num_threads=4;options.inter_op_num_threads=1
        options.execution_mode=ort.ExecutionMode.ORT_SEQUENTIAL
        self.session=ort.InferenceSession(str(model_path),sess_options=options,providers=['CPUExecutionProvider'])
        self.tokenizer=AutoTokenizer.from_pretrained(str(tokenizer_path),local_files_only=True,trust_remote_code=False)
        self.max_length=max_length;self.batch_size=batch_size
        self.input_names={i.name for i in self.session.get_inputs()}

    def score(self,query,passages,*,sort_by_length=False):
        started=time.perf_counter()
        self.last_profile=dict(tokenize_ms=0.0,inference_ms=0.0,batches=[])
        pairs=[[query,p] for p in passages]
        if not pairs:return dict(scores=[],token_lengths=[],truncated=0)
        tick=time.perf_counter()
        lengths=[len(t) for t in self.tokenizer(pairs,truncation=False)['input_ids']]
        self.last_profile['tokenize_ms']=(time.perf_counter()-tick)*1000
        scores=[None]*len(pairs)
        for indices in batch_indices([min(n,self.max_length) for n in lengths],self.batch_size,sort_by_length=sort_by_length):
            tick=time.perf_counter()
            inputs=self.tokenizer([pairs[i] for i in indices],padding=True,truncation='only_second',return_tensors='np',max_length=self.max_length)
            token_ms=(time.perf_counter()-tick)*1000
            self.last_profile['tokenize_ms']+=token_ms
            tick=time.perf_counter()
            values=self.session.run(None,{k:v for k,v in inputs.items() if k in self.input_names})[0].reshape(-1).tolist()
            infer_ms=(time.perf_counter()-tick)*1000
            self.last_profile['inference_ms']+=infer_ms
            self.last_profile['batches'].append(dict(size=len(indices),max_tokens=max(lengths[i] for i in indices),
                tokenize_ms=round(token_ms,3),inference_ms=round(infer_ms,3)))
            for index,value in zip(indices,values,strict=True):scores[index]=value
        if not all(math.isfinite(s) for s in scores):raise ValueError('Invalid ONNX logits')
        self.last_profile['score_ms']=(time.perf_counter()-started)*1000
        return dict(scores=scores,token_lengths=lengths,truncated=sum(n>self.max_length for n in lengths))
