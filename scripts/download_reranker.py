"""Download the pinned official BGE reranker; no corpus upload or inference API."""
import hashlib
import json
from pathlib import Path
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
REPO='BAAI/bge-reranker-v2-m3'
REVISION='953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e'
TARGET=ROOT/'models/bge-reranker-v2-m3'
FILES=['config.json','model.safetensors','sentencepiece.bpe.model','special_tokens_map.json','tokenizer.json','tokenizer_config.json']


def digest(path):
    with path.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()


def main():
    with urllib.request.urlopen(f'https://huggingface.co/api/models/{REPO}/revision/{REVISION}?blobs=true',timeout=30) as r:meta=json.load(r)
    assert meta['sha']==REVISION
    entries={x['rfilename']:x for x in meta['siblings']};verified={}
    for name in FILES:
        info=entries[name];dest=TARGET/name;dest.parent.mkdir(parents=True,exist_ok=True)
        expected=info.get('lfs',{}).get('sha256')
        if not (dest.exists() and expected and dest.stat().st_size==info['size'] and digest(dest)==expected):
            part=dest.with_suffix(dest.suffix+'.part')
            print('Downloading',name,info['size'],'bytes',flush=True)
            with urllib.request.urlopen(f'https://huggingface.co/{REPO}/resolve/{REVISION}/{name}',timeout=90) as r,part.open('wb') as f:
                count=last=0
                while block:=r.read(1024*1024):
                    f.write(block);count+=len(block)
                    if count-last>=256*1024*1024:
                        print(name,round(count/info['size']*100),'%',flush=True);last=count
            if part.stat().st_size!=info['size'] or (expected and digest(part)!=expected):raise ValueError('Model checksum mismatch')
            part.replace(dest)
        verified[name]=dict(size=dest.stat().st_size,sha256=digest(dest))
    (TARGET/'download-manifest.json').write_text(json.dumps(dict(repo=REPO,revision=REVISION,files=verified),indent=2),encoding='utf-8')
    print('Pinned reranker verified',flush=True)


if __name__=='__main__':main()
