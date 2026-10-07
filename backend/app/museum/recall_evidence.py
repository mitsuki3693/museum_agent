"""Offline bridge from work-level translated fields back to original chunks."""
import copy
import re
from .semantic_chunks import dense_views
from .search_fields import FIELD_WEIGHTS
from .recall_fields import lexical_query
from app.retrieval.bm25 import tokenize

VERSION='translated-field-original-anchor-v1'


def bridge_candidate(query,candidate,source,annotation=None):
    if candidate['source_id']!=source['_id']:raise ValueError('Wrong source')
    if annotation and (annotation['source_id']!=source['_id'] or annotation['source_hash']!=source['source_hash']):
        raise ValueError('Stale annotation')
    result=copy.deepcopy(candidate)
    lane=result.get('lanes',{}).get('lexical')
    if not lane:return result,dict(reason='no_lexical_lane')
    chunks=dense_views([source])['original']
    by_id={c['_id']:c for c in chunks}
    if lane['best_chunk_id'] in by_id:
        if lane['best_content']!=by_id[lane['best_chunk_id']]['content']:raise ValueError('Changed original evidence')
        return result,dict(reason='original_chunk')
    terms=set(tokenize(lexical_query(query)))
    proposals=[]
    for name,field in (annotation or {}).get('fields',{}).items():
        overlap=terms.intersection(tokenize(field['text']))
        if not overlap or field.get('evidence_scope','content')!='content':continue
        for quote in field['evidence_quotes']:
            if quote not in source['content']:raise ValueError('Missing anchor')
            proposals.append((len(overlap)*FIELD_WEIGHTS[name],name,quote))
    prefix=source['title']+'\n'
    bodies=[re.sub(r'\s+',' ',c['content'][len(prefix):]).strip() for c in chunks]
    joined=' '.join(bodies)
    starts=[];offset=0
    for body in bodies:starts.append(offset);offset+=len(body)+1
    for _,name,quote in sorted(proposals,key=lambda p:-p[0]):
        needle=re.sub(r'\s+',' ',quote).strip();start=joined.find(needle)
        if start<0:continue
        selected=[i for i,begin in enumerate(starts) if begin<start+len(needle) and begin+len(bodies[i])>start]
        if not selected:continue
        chunk=chunks[selected[0]]
        lane.update(best_chunk_id=chunk['_id'],best_content=chunk['content'])
        return result,dict(reason='source_anchor',field=name,chunk_ids=[chunks[i]['_id'] for i in selected])
    # The title is already packed by contextual_evidence. Never fabricate a
    # chunk ID or send translated annotations as original museum prose.
    del result['lanes']['lexical']
    return result,dict(reason='title_or_no_body_anchor')
