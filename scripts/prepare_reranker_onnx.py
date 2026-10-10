"""Export the already verified local BGE weights; no remote code/model fetch."""
import argparse
import hashlib
import json
from pathlib import Path
import time

from app.museum.config import ROOT
from app.museum.local_reranker import LocalPairReranker,MODEL_REVISION


def sha(path):
    with path.open('rb') as handle:return hashlib.file_digest(handle,'sha256').hexdigest()


def main(destination):
    import torch
    import onnxruntime
    from onnxruntime.quantization import quantize_dynamic,QuantType
    if not destination.resolve().is_relative_to((ROOT/'models').resolve()):raise ValueError('Use project models directory')
    if destination.exists():raise FileExistsError('Preserve export')
    source=ROOT/'models/bge-reranker-v2-m3'
    manifest=json.loads((source/'download-manifest.json').read_bytes())
    assert manifest['revision']==MODEL_REVISION
    for name,info in manifest['files'].items():assert sha(source/name)==info['sha256']
    torch.set_num_threads(4)
    model=LocalPairReranker(source)
    class Logits(torch.nn.Module):
        def __init__(self):super().__init__();self.model=model.model
        def forward(self,input_ids,attention_mask):return self.model(input_ids=input_ids,attention_mask=attention_mask,return_dict=True).logits
    destination.mkdir(parents=True)
    example=model.tokenizer('blue vase','A porcelain vase.',return_tensors='pt')
    started=time.perf_counter()
    # Legacy exporter supports XLM-R dynamic sequence and external >2GB data.
    torch.onnx.export(Logits().eval(),(example['input_ids'],example['attention_mask']),str(destination/'model.onnx'),
        input_names=['input_ids','attention_mask'],output_names=['logits'],opset_version=17,dynamo=False,
        external_data=True,dynamic_axes={'input_ids':{0:'batch',1:'sequence'},'attention_mask':{0:'batch',1:'sequence'},'logits':{0:'batch'}})
    print('FP32 export complete',flush=True)
    del model
    import gc;gc.collect()
    quantize_dynamic(str(destination/'model.onnx'),str(destination/'model-int8.onnx'),
        weight_type=QuantType.QInt8,per_channel=True,reduce_range=True,
        op_types_to_quantize=['MatMul','Gemm'],use_external_data_format=True)
    report=dict(revision=MODEL_REVISION,source_manifest_sha256=sha(source/'download-manifest.json'),
        torch=torch.__version__,onnxruntime=onnxruntime.__version__,quantization='dynamic-int8-per-channel-reduced-range-matmul-gemm',
        export_seconds=round(time.perf_counter()-started,3),files={})
    for p in destination.iterdir():
        if p.is_file():report['files'][p.name]=dict(sha256=sha(p),bytes=p.stat().st_size)
    (destination/'export-manifest.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(dict(complete=True,seconds=report['export_seconds'])),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    main(parser.parse_args().output)
