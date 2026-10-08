"""Opt-in Chinese field retrieval with validated original-source anchors."""
import hashlib
import json
import re

from app.retrieval.bm25f import BM25FIndex
from .search_fields import FIELD_WEIGHTS, field_documents
from .recall_fields import lexical_query
from .recall_evidence import bridge_candidate
from .work_fusion import fuse_works

VERSION='chinese-fields-original-bridge-v1'


class ChineseRecall:
    def __init__(self, path, records, accepted_fields, metadata):
        raw=path.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=metadata['sha256']:
            raise ValueError('Search manifest changed after validation')
        self.annotations={e['source_id']:dict(e,fields={k:v for k,v in e['fields'].items()
                          if k in accepted_fields.get(e['source_id'],{})})
                          for e in json.loads(raw)['records']}
        self.index=BM25FIndex(FIELD_WEIGHTS)
        self.index.index(field_documents(records,accepted_fields))

    @staticmethod
    def applies(query):return bool(re.search('[\u4e00-\u9fff]',query))

    def pool(self, query, bm25, dense_hits):
        lexical=self.index.search(lexical_query(query),top_k=25) if self.applies(query) else bm25.search(query,top_k=25)
        return fuse_works(lexical,dense_hits)

    def bridge(self,query,candidates,sources):
        records={s['_id']:s for s in sources};result=[];audits=[]
        for candidate in candidates:
            sid=candidate['source_id']
            bridged,audit=bridge_candidate(query,candidate,records[sid],self.annotations.get(sid))
            result.append(bridged);audits.append(dict(source_id=sid,**audit))
        return result,audits
