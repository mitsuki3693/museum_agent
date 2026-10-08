from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from app.museum.local_reranker import LocalPairReranker, batch_indices


def test_length_batches_are_stable_and_cover_every_candidate_once():
    assert batch_indices([9, 2, 9, 1, 5], 2, sort_by_length=True)==[[3, 1], [4, 0], [2]]
    assert batch_indices([9, 2, 9, 1, 5], 2)==[[0, 1], [2, 3], [4]]
    assert batch_indices([], 4, sort_by_length=True)==[]
    with pytest.raises(ValueError):batch_indices([1], 0)


class Values:
    def __init__(self, values):self.values=values
    def reshape(self, *args):return self
    def float(self):return self
    def tolist(self):return self.values


def fake_reranker():
    batches=[]
    obj=LocalPairReranker.__new__(LocalPairReranker)
    obj.torch=SimpleNamespace(inference_mode=nullcontext)
    obj.batch_size=2;obj.max_length=512
    def tokenizer(pairs, **kwargs):
        if not kwargs.get('return_tensors'):
            return {'input_ids':[list(range(len(p))) for _,p in pairs]}
        ids=[int(p.split(':')[0]) for _,p in pairs];batches.append(ids)
        return dict(ids=ids)
    obj.tokenizer=tokenizer
    obj.model=lambda ids,**kwargs:SimpleNamespace(logits=Values([100-i for i in ids]))
    return obj,batches


def test_actual_score_restores_original_candidate_order_after_batching():
    model,batches=fake_reranker()
    passages=['0:'+'a'*9,'1:b','2:'+'c'*5,'3:']
    normal=model.score('question',passages)
    assert batches==[[0,1],[2,3]]
    batches.clear()
    sorted_result=model.score('question',passages,sort_by_length=True)
    assert batches==[[3,1],[2,0]]
    assert sorted_result==normal
    assert sorted_result['scores']==[100,99,98,97]
    assert sorted_result['token_lengths']==[11,3,7,2]


def test_empty_input_skips_model_and_missing_outputs_fail():
    model,batches=fake_reranker()
    assert model.score('question',[],sort_by_length=True)['scores']==[]
    assert batches==[]
    model.model=lambda **kwargs:SimpleNamespace(logits=Values([]))
    with pytest.raises(ValueError):model.score('question',['0:a'],sort_by_length=True)
