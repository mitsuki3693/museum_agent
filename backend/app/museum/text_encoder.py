"""Explicit encoder profiles for offline retrieval comparisons; not live configuration."""
from dataclasses import dataclass
import numpy as np


@dataclass(frozen=True)
class EncoderProfile:
    model_id: str
    revision: str
    dimension: int
    query_prefix: str = ''
    passage_prefix: str = ''


MINILM = EncoderProfile('sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2',
                       'e8f8c211226b894fcb81acc59f3b34ba3efd5f42',384)
E5 = EncoderProfile('intfloat/multilingual-e5-base',
                   'd128750597153bb5987e10b1c3493a34e5a4502a',768,'query: ','passage: ')


class RetrievalEncoder:
    """Caller supplies a locally loaded model. Never downloads or falls back to hash."""
    def __init__(self, model, profile: EncoderProfile):
        if model.get_sentence_embedding_dimension() != profile.dimension:
            raise ValueError('Encoder profile dimension mismatch')
        self.model, self.profile = model, profile

    def inputs(self, texts, *, is_query):
        if any(not isinstance(t,str) for t in texts):
            raise ValueError('Expected text inputs')
        prefix=self.profile.query_prefix if is_query else self.profile.passage_prefix
        return [prefix+t for t in texts]

    def _encode(self,texts,*,is_query):
        if not texts:
            return np.empty((0,self.profile.dimension),dtype=np.float32)
        values=np.asarray(self.model.encode(self.inputs(texts,is_query=is_query),
            normalize_embeddings=True,convert_to_numpy=True,batch_size=32,
            show_progress_bar=False,prompt=''))
        if values.shape!=(len(texts),self.profile.dimension) or not np.isfinite(values).all():
            raise ValueError('Invalid encoder output shape or values')
        if not np.allclose(np.linalg.norm(values,axis=1),1,atol=1e-4):
            raise ValueError('Encoder vectors must be normalized')
        return values

    def encode_queries(self,texts):
        return self._encode(texts,is_query=True)

    def encode_passages(self,texts):
        return self._encode(texts,is_query=False)
