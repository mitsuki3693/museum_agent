"""Derived dense-index views. Original sources and lexical chunks stay intact.

Filtered is opt-in; normalized remains an offline-only experiment.
"""
import textwrap

VERSION = 'semantic-content-v1'
# Exact, source-schema labels only. Never discard arbitrary text before a colon.
METADATA_LABELS = frozenset({
    'Title','Museum number','Maker','Date','Place','Materials and techniques','materialsAndTechniques',
    '名称 Title','藏品编号 Object ID','作者 Artist','年代 Date','材质 Medium','尺寸 Dimensions',
    '创作地 Origin','入藏信息 Credit line','馆藏编号 Accession number','分类 Classification','馆藏部门 Department',
})
BODY_LABELS = frozenset({'briefDescription','summaryDescription','physicalDescription','objectHistory','馆方介绍 Description'})

# Opt-in narrower experiment. Descriptive fields remain semantic evidence.
ADMIN_VIEW_VERSION = 'administrative-filter-v1'
ADMIN_LABELS = frozenset({'Museum number', '藏品编号 Object ID', '馆藏编号 Accession number',
                         '尺寸 Dimensions', '入藏信息 Credit line', '馆藏部门 Department'})


def administrative_view(records):
    """Exclude only exact administrative labels, preserving source text and IDs.

    This is an experimental view, not the default application configuration.
    Dimensions may themselves be useful visitor queries; activation needs wider
    acceptance than the author/material/date development set alone.
    """
    views = dense_views(records)
    excluded = [a for a in views['audit']
                if a['action'] == 'metadata_dense_excluded' and a['label'] in ADMIN_LABELS]
    excluded_ids = {a['chunk_id'] for a in excluded}
    return dict(version=ADMIN_VIEW_VERSION, excluded=excluded,
                chunks=[c for c in views['original'] if c['_id'] not in excluded_ids])


def dense_views(records):
    """Preserve legacy chunk IDs so RRF overlap is not accidentally changed.

    Filter entire metadata lines before considering wrapped continuation pieces.
    No-description records remain available to lexical/exact routes, but receive
    no invented semantic content or synthetic fallback vector.
    """
    original, filtered, normalized, audit = [], [], [], []
    no_body=[]
    for record in records:
        serial=0;kept=0
        for line_number,line in enumerate(record['content'].splitlines(),1):
            prefix,separator,_=line.partition(':')
            prefix=prefix.strip() if separator else ''
            is_meta=prefix in METADATA_LABELS
            for part_number,piece in enumerate(textwrap.wrap(line,width=220)):
                chunk=dict(_id=f"{record['_id']}::{serial}",source_id=record['_id'],content=record['title']+'\n'+piece)
                serial+=1;original.append(chunk)
                if is_meta:
                    audit.append(dict(chunk_id=chunk['_id'],source_id=record['_id'],line=line_number,label=prefix,action='metadata_dense_excluded'))
                    continue
                filtered.append(chunk)
                clean=piece
                if part_number==0 and prefix in BODY_LABELS:
                    clean=piece.split(':',1)[1].lstrip()
                if clean.strip():
                    normalized.append({**chunk,'content':record['title']+'\n'+clean});kept+=1
                if clean!=piece:
                    audit.append(dict(chunk_id=chunk['_id'],source_id=record['_id'],line=line_number,label=prefix,action='body_label_removed'))
        if not kept:no_body.append(record['_id'])
    return dict(original=original,filtered=filtered,normalized=normalized,audit=audit,no_body_source_ids=no_body,version=VERSION)
