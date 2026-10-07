"""Fetch one pinned public E5 snapshot; never uploads corpus or calls an API model."""
import hashlib
import json
from pathlib import Path
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
REPO='intfloat/multilingual-e5-base'
REVISION='d128750597153bb5987e10b1c3493a34e5a4502a'
TARGET=ROOT/'models/multilingual-e5-base'
FILES=['config.json','modules.json','sentence_bert_config.json','1_Pooling/config.json',
       'model.safetensors','sentencepiece.bpe.model','special_tokens_map.json','tokenizer.json','tokenizer_config.json']


def digest(path):
    with path.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()


def main():
    with urllib.request.urlopen(f'https://huggingface.co/api/models/{REPO}/revision/{REVISION}?blobs=true',timeout=30) as r:
        meta=json.load(r)
    assert meta['sha']==REVISION
    entries={f['rfilename']:f for f in meta['siblings']}
    evidence={}
    for name in FILES:
        info=entries[name];dest=TARGET/name
        dest.parent.mkdir(parents=True,exist_ok=True)
        expected=info.get('lfs',{}).get('sha256')
        # Small config files are fetched from the pinned commit again; LFS
        # assets are reused only after checking the official content hash.
        if not (dest.exists() and expected and dest.stat().st_size==info['size'] and digest(dest)==expected):
            part=dest.with_suffix(dest.suffix+'.part')
            print('Downloading',name,info['size'],'bytes',flush=True)
            with urllib.request.urlopen(f'https://huggingface.co/{REPO}/resolve/{REVISION}/{name}',timeout=60) as r, part.open('wb') as f:
                count=0;last=0
                while block:=r.read(1024*1024):
                    f.write(block);count+=len(block)
                    if count-last>=128*1024*1024:
                        print(name,round(count/info['size']*100),'%',flush=True);last=count
            if part.stat().st_size!=info['size'] or (expected and digest(part)!=expected):
                raise ValueError('Downloaded file size/hash mismatch: '+name)
            part.replace(dest)
        evidence[name]=dict(size=dest.stat().st_size,sha256=digest(dest))
    (TARGET/'download-manifest.json').write_text(json.dumps(dict(repo=REPO,revision=REVISION,files=evidence),indent=2),encoding='utf-8')
    print('Pinned model verified:',TARGET,flush=True)


if __name__=='__main__':main()
