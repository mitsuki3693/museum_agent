"""Make a local source-review sheet; private museum images never enter Git."""
import html
import json
import os
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def main():
    folder=ROOT/'data/private/photo-stage-30-v1'
    data=json.loads((folder/'manifest.json').read_bytes())
    refs=json.loads((folder.parent/'va-pilot-100-v1-references.json').read_bytes())['references']
    sections=[]
    for n,case in enumerate(data['cases'],1):
        def tag(path,label):
            relative=os.path.relpath(path,folder).replace('\\','/')
            return f'<figure><img loading="lazy" src="{html.escape(relative)}" alt="{html.escape(label)}"><figcaption>{html.escape(label)}</figcaption></figure>'
        images=tag(ROOT/case['path'],'测试照片')
        if case['expected_source_id']:
            ref=next(r for r in refs if r['source_id']==case['expected_source_id'])
            images+=tag(folder.parent/ref['path'],'图库参考图（没有更换）')
        sections.append(f'''<article><h2>{n:02d} · {html.escape(case['title'])}</h2>
          <p>{'库内作品' if case['expected_source_id'] else '库外作品'} · {html.escape(case['accession'])} · {html.escape(case['group'])}</p>
          <div class="images">{images}</div><p>{html.escape(case['review_note'])}</p>
          <a href="{html.escape(case['source_url'])}" target="_blank" rel="noreferrer">打开馆方来源</a>
          <p>来源：{html.escape(case['author'])}；许可：{html.escape(case['license'])}。人工复核：待完成。</p></article>''')
    document='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
    <title>MUSE · 30 张开发图片来源复核</title><style>
    body{font:16px/1.6 system-ui,sans-serif;margin:0 auto;padding:24px;max-width:1000px;background:#faf9f7;color:#241e28}
    article{border-top:1px solid #ccc;padding:28px 0}.images{display:flex;flex-wrap:wrap;gap:24px}figure{margin:0;flex:1 1 280px}
    img{display:block;width:100%;height:360px;object-fit:contain;background:#eee}a{color:#56316f}figcaption{margin-top:8px}
    </style><h1>30 张开发图片来源复核</h1><p>20 张库内不同视角／局部图，10 张库外相似作品。仅本地研究使用。
    已由代码助手核对来源与图片，尚未由人复核；这不是盲测集或识别准确率报告。图片未上传 GitHub。</p>'''+''.join(sections)+'</html>'
    (folder/'review.html').write_text(document,encoding='utf-8')
    print('Local review sheet created: 30 cases; no network calls')


if __name__=='__main__':main()
