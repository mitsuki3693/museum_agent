import numpy as np
import pytest
from app.museum.text_encoder import RetrievalEncoder, E5, MINILM


class FakeModel:
    def __init__(self,dimension):self.dimension=dimension;self.calls=[]
    def get_sentence_embedding_dimension(self):return self.dimension
    def encode(self,texts,**kwargs):
        self.calls.append((texts,kwargs))
        result=np.zeros((len(texts),self.dimension));result[:,0]=1
        return result


def test_e5_prefixes_are_asymmetric_including_chinese_and_empty_batch():
    model=FakeModel(768);encoder=RetrievalEncoder(model,E5)
    encoder.encode_queries(['花瓶']);encoder.encode_passages(['A vase'])
    assert model.calls[0][0]==['query: 花瓶']
    assert model.calls[1][0]==['passage: A vase']
    assert model.calls[0][1]['normalize_embeddings'] is True
    assert model.calls[0][1]['prompt']==''  # Avoid an implicit second prefix.
    assert encoder.encode_queries([]).shape==(0,768)
    assert len(model.calls)==2


def test_minilm_keeps_original_inputs_and_no_cross_model_dimensions():
    model=FakeModel(384);encoder=RetrievalEncoder(model,MINILM)
    encoder.encode_queries(['花瓶']);encoder.encode_passages(['A vase'])
    assert [c[0] for c in model.calls]==[['花瓶'],['A vase']]
    with pytest.raises(ValueError):RetrievalEncoder(model,E5)


@pytest.mark.parametrize('output',[np.zeros((1,768)),np.full((1,768),float('nan')),np.zeros((1,384))])
def test_no_invalid_vector_or_hash_fallback(output):
    model=FakeModel(768);model.encode=lambda *a,**k:output
    with pytest.raises(ValueError):RetrievalEncoder(model,E5).encode_queries(['test'])
