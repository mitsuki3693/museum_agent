"""Reject shorter budgets that remove previously retained required evidence."""
import json
from app.museum.config import ROOT
from app.museum.rerank_worker import score_request


def main():
    from transformers import AutoTokenizer
    class PackOnly:
        tokenizer=AutoTokenizer.from_pretrained(ROOT/'models/bge-reranker-v2-m3',local_files_only=True)
        def score(self,q,passages,**kwargs):
            return dict(scores=[0.0]*len(passages),truncated=0,
                token_lengths=[len(self.tokenizer(q,p,truncation=False)['input_ids']) for p in passages])
    model=PackOnly()
    data=json.loads((ROOT/'eval/private/rerank-latency-input-v1.json').read_bytes());assert data['complete']
    results=[]
    for row in data['rows']:
        if not row['request']:continue
        base=score_request(model,row['request'],sort_by_length=True,evidence_controls=True)
        critical=lambda e:{(item['reason'],item['chunk_id']) for item in e['included'] if item['reason']=='attribute' or item['reason'].startswith('retrieved_') or item['reason']=='title'}
        for budget in (192,160):
            new=score_request(model,row['request'],sort_by_length=True,evidence_controls=True,context_budget=budget)
            lost=[a['source_id'] for a,b in zip(base['evidence'],new['evidence'],strict=True) if critical(a)-critical(b)]
            results.append(dict(id=row['id'],budget=budget,candidates_losing_required_evidence=lost))
    report=dict(rows=results,summary={str(b):dict(queries_affected=sum(bool(r['candidates_losing_required_evidence']) for r in results if r['budget']==b),
        candidates_affected=sum(len(r['candidates_losing_required_evidence']) for r in results if r['budget']==b)) for b in (192,160)})
    with (ROOT/'eval/private/rerank-budget-v1.json').open('x',encoding='utf-8') as f:json.dump(report,f,indent=2)
    print(json.dumps(report['summary']),flush=True)


if __name__=='__main__':main()
