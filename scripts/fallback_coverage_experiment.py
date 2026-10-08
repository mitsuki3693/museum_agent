"""Require majority lexical coverage before replacing the original fallback.

Coverage is a routing heuristic, never a probability or proof of relevance.
Only the top five field candidates count; model rerank pools are unaffected.
"""
import re

from app.retrieval.bm25 import tokenize
from app.museum.recall_fields import lexical_query

VERSION='majority-query-coverage-v1'
FUNCTION_WORDS=frozenset({'还有','一个','一件','那个','这个','哪些','什么','哪里','怎么','怎样','请问','介绍','一下'})


def fallback_gate(query, field_index):
    terms={t for t in tokenize(lexical_query(query)) if len(t)>=2 and t not in FUNCTION_WORDS}
    terms={t for t in terms if re.search(r'[\w\u4e00-\u9fff]',t)}
    audit=dict(version=VERSION,decision='original',query_terms=sorted(terms),matched_terms=[],
               source_id=None,coverage=0.0,threshold=0.5)
    if not terms:return audit
    for hit in field_index.search(lexical_query(query),top_k=5):
        fields=field_index.docs[hit['source_id']]['search_fields']
        present={t for text in fields.values() for t in tokenize(text)}
        matched=terms & present
        coverage=len(matched)/len(terms)
        if coverage>audit['coverage']:
            audit.update(source_id=hit['source_id'],matched_terms=sorted(matched),coverage=coverage)
    if audit['coverage']>=audit['threshold']:audit['decision']='chinese_fields'
    return audit
