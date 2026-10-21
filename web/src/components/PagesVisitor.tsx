"use client";
import {useEffect, useRef, useState} from "react";
import ArtworkCandidate from "./ArtworkCandidate";
import {ListenButton} from "./MuseumSpeech";
import {sitePath} from "@/lib/site-mode";
import {askPublicAi,publicAiUrl,type PublicMessage} from "@/lib/public-ai";
import {demoWorks, demoAnswer, findDemoWorks, sourceFor, type DemoWork, type DemoStyle} from "@/lib/pages-catalogue";
import "./pages-demo.css";

type Turn = {question:string; answer:string; work?:DemoWork; candidates?:readonly DemoWork[];kind?:"ai"|"error"};
export default function PagesVisitor(){
  const [selected,setSelected]=useState<DemoWork|null>(null), [turns,setTurns]=useState<Turn[]>([]);
  const [query,setQuery]=useState(""),[photo,setPhoto]=useState<File|null>(null),[preview,setPreview]=useState("");
  const [error,setError]=useState(""),[browse,setBrowse]=useState(false),[feedback,setFeedback]=useState<Record<number,string>>({});
  const end=useRef<HTMLDivElement>(null), input=useRef<HTMLInputElement>(null);
  const [busy,setBusy]=useState(false);
  const pending=useRef<AbortController|null>(null),generation=useRef(0);
  useEffect(()=>{if(!photo){setPreview("");return;} const url=URL.createObjectURL(photo);setPreview(url);return()=>URL.revokeObjectURL(url);},[photo]);
  useEffect(()=>{if(turns.length)end.current?.scrollIntoView({block:"end"});},[turns]);
  useEffect(()=>()=>{window.speechSynthesis?.cancel();pending.current?.abort();},[]);
  function append(turn:Turn){setTurns(old=>[...old,turn]);}
  function cancel(){generation.current++;pending.current?.abort();pending.current=null;setBusy(false);}
  async function generate(work:DemoWork,question:string,style:DemoStyle){
    if(pending.current)return;
    const controller=new AbortController(),version=generation.current;
    pending.current=controller;setBusy(true);
    const history:PublicMessage[]=turns.filter(t=>t.work?.id===work.id&&t.kind!=="error").slice(-3).flatMap(t=>[{role:"user" as const,content:t.question.slice(0,1200)},{role:"assistant" as const,content:t.answer.slice(0,1200)}]);
    const timer=window.setTimeout(()=>controller.abort(),30000);
    try{const result=await askPublicAi({workId:work.id,question,style,history},controller.signal);if(version===generation.current)append({question,answer:result.answer,work,kind:"ai"});}
    catch(e){if(version===generation.current)append({question,answer:controller.signal.aborted?"等待超时，可以稍后再试或查看预置讲解。":e instanceof Error?e.message:"网络暂不可用，请稍后再试。",work,kind:"error"});}
    finally{window.clearTimeout(timer);if(version===generation.current){pending.current=null;setBusy(false);}}
  }
  function choose(work:DemoWork,style:DemoStyle="brief"){
    if(busy)return;
    setSelected(work);setBrowse(false);setPhoto(null);
    const question=style==="children"?"讲给孩子听":style==="deep"?"再深入一点":`介绍《${work.name}》`;
    if(publicAiUrl){void generate(work,question,style);return;}
    append({question,answer:work[style],work});
  }
  function send(text=query){
    if(!text.trim()||busy)return;
    setQuery("");
    if(/厕所|出口|楼梯|电梯|服务台|路线/.test(text)){append({question:text,answer:"请打开下方的路线示意，选择厕所、出口、楼梯或服务台。平面图与定位均为虚构案例，不能用于真实博物馆导航。"});return;}
    if(/比较|区别|相比/.test(text)){append({question:text,answer:"本次体验暂不支持跨作品比较。可以分别打开作品讲解与馆方来源；这里不会把未准备的内容伪装成 AI 回答。"});return;}
    const matches=findDemoWorks(text);
    if(matches.length&&!(publicAiUrl&&selected&&matches.length===1&&matches[0].id===selected.id)){setSelected(null);append({question:text,answer:"这是你想了解的作品吗？请确认后继续。以下是预置馆藏匹配结果。",candidates:matches});return;}
    if(/换一件|找一件|帮我找|我要找/.test(text)){setSelected(null);append({question:text,answer:"本页收录三件示例作品，请选择一件继续。",candidates:demoWorks});return;}
    if(selected){if(publicAiUrl){void generate(selected,text,"brief");return;}append({question:text,answer:demoAnswer(selected,text,"brief"),work:selected});return;}
    append({question:text,answer:"请先选择睡莲、卧室或大碗岛中的一件作品，或试试输入“睡莲”。",candidates:demoWorks});
  }
  function clear(){cancel();setSelected(null);setTurns([]);setPhoto(null);setQuery("");setError("");setBrowse(false);setFeedback({});window.speechSynthesis?.cancel();}
  function selectPhoto(file?:File){
    if(!file)return;
    if(!["image/jpeg","image/png","image/webp"].includes(file.type)||file.size>8*1024*1024){setError("请选择 8 MB 以内的 JPG、PNG 或 WebP。照片只在本机预览。");return;}
    setError("");setPhoto(file);setBrowse(true);
  }
  const cards=(works:readonly DemoWork[]) => <div className="discovery-candidates">{works.map(work=><ArtworkCandidate key={work.id} work={{id:work.id,title:work.title,display_title:work.name,artist:work.artist,accession_number:work.number,image_url:sitePath(`/collection/${work.id}.jpg`),source_url:sourceFor(work)}} onConfirm={()=>choose(work)} disabled={busy} actionLabel="选择这件 · 查看讲解"/>)}</div>;
  return <main className="chat-shell pages-demo">
    <header className="chat-header"><a className="brand" href={sitePath("/")} aria-label="MUSE 首页"><strong className="brand-wordmark">MUSE<span className="brand-dot">.</span></strong></a><div className="header-actions"><button className="quiet" onClick={()=>setBrowse(!browse)}>选件作品</button><button className="quiet" onClick={clear}>新对话</button><a href={sitePath("staff-demo/")}>馆方端</a></div></header>
    <section className="chat-log" aria-label="博物馆演示对话"><div className="chat-width">
      <div className="pages-disclosure"><strong>{publicAiUrl?"AI 讲解体验":"交互演示 · 无后端"}</strong><span>{publicAiUrl?"提问及当前作品最近3轮对话将经 Cloudflare 发给 DeepSeek。回答可能有误，请核对来源；勿输入个人敏感信息。照片不上传。":"讲解为预置示例，不调用 AI；照片、对话与反馈不上传，刷新即清空。"}</span></div>
      <div className="welcome-message"><span className="assistant-avatar" aria-hidden="true">m</span><div><h1>这一件，有什么故事？</h1><p>从一件作品开始，换一种讲法，再发现一个细节。</p>
        {!turns.length&&<div className="starter-works">{demoWorks.map(work=><button key={work.id} disabled={busy} onClick={()=>choose(work)}><img src={sitePath(`/collection/${work.id}.jpg`)} alt=""/><span>{work.name}<small>从这件开始</small></span></button>)}</div>}
      </div></div>
      {preview&&<div className="pages-photo"><img src={preview} alt="你选择的照片，仅本机预览"/><div><strong>照片仅预览，没有运行识别</strong><p>从下方手动选择一件示例，体验确认后讲解的流程。选择不代表照片识别成功。</p><button className="quiet" onClick={()=>setPhoto(null)}>移除照片</button></div></div>}
      {browse&&<section aria-label="示例馆藏"><h2>选择一件示例作品</h2>{cards(demoWorks)}</section>}
      <div className="turns" aria-live="polite">{turns.map((turn,i)=><article className="turn" key={i}><div className="user-message"><p>{turn.question}</p></div><div className="assistant-message"><span className="assistant-avatar" aria-hidden="true">m</span><div className="answer"><div className="answer-label">MUSE <span>{turn.kind==="ai"?"AI 生成 · 请核对来源":turn.kind==="error"?"暂未完成":"预置示例"}</span></div><p className="answer-text">{turn.answer}</p>{turn.candidates&&cards(turn.candidates)}
        {turn.work&&<><div className="answer-actions">{turn.kind!=="error"&&<ListenButton text={turn.answer}/>}<button className="quiet" disabled={busy} onClick={()=>choose(turn.work!,"brief")}>简明版</button><button className="quiet" disabled={busy} onClick={()=>choose(turn.work!,"children")}>讲给孩子听</button><button className="quiet" disabled={busy} onClick={()=>choose(turn.work!,"deep")}>深入一点</button>{publicAiUrl&&<button className="quiet" onClick={()=>{cancel();setSelected(turn.work!);append({question:"查看预置讲解",answer:turn.work!.brief,work:turn.work});}}>查看预置讲解</button>}</div><details className="pages-source"><summary>查看资料来源与示例说明</summary><p>{turn.kind==="ai"?"AI 根据本项目整理的 AIC 公开资料摘要生成；并非官方讲解词，也未经过逐条事实核验。请结合馆方来源判断。":"依据芝加哥艺术博物馆公开编目，由本项目编写中文示例；不是馆方官方讲解词。"}</p><a href={sourceFor(turn.work)} target="_blank" rel="noreferrer">{turn.work.title} · 馆方页面</a></details></>}
        <div className="pages-feedback"><span>体验反馈（仅本页）</span>{["有帮助","没有解答"].map(kind=><button className="quiet" key={kind} aria-pressed={feedback[i]===kind} onClick={()=>setFeedback(old=>({...old,[i]:kind}))}>{kind}{feedback[i]===kind?" · 已选":""}</button>)}</div>
      </div></div></article>)}</div>
      <nav className="pages-links" aria-label="更多演示"><a href={sitePath("route-demo/")}>路线与设施示意</a><a href={sitePath("conservation-demo/")}>文保工作台</a><a href="https://github.com/mitsuki3693/museum_agent" target="_blank" rel="noreferrer">项目代码与评测</a></nav>
      <details className="pages-about"><summary>关于这个版本与图片来源</summary><p>MUSE 是个人项目，不代表 V&A 或 AIC 官方产品。本页只展示三件 AIC 作品；完整本地版的检索、视觉核对及运行记录未在这里执行。</p><p>三张图片为 AIC 标记的公共领域作品，按 CC0 开放。中文讲解为项目自编摘要，来源可从每件作品查看。路线图和客流为项目自编的虚构案例。</p><a href="https://www.artic.edu/open-access/open-access-images" target="_blank" rel="noreferrer">AIC 开放图片说明</a><p>真实部署与评测范围请查看仓库文档。本页操作不作为真实模型评测成绩。{publicAiUrl&&" AI调用按网络地址限制频率和日额度，共享网络会共享额度。服务端只保留限额计数和每天变化的匿名标识，不主动保存对话；Cloudflare和DeepSeek按各自服务规则处理请求。"}</p></details>
      {busy&&<p role="status">正在根据资料生成讲解… <button className="quiet" onClick={cancel}>取消</button></p>}<div ref={end}/>
    </div></section>
    <footer className="chat-footer"><div className="chat-width">{selected&&<div className="context-chip"><span>正在聊 · {selected.name}</span><button className="quiet" onClick={()=>{cancel();setSelected(null);setBrowse(true);}}>换一件</button></div>}
      <form className="composer" onSubmit={e=>{e.preventDefault();send();}}><label className="sr-only" htmlFor="demo-question">输入示例问题</label><textarea id="demo-question" rows={2} maxLength={600} placeholder={selected?"试试：作者是谁？什么年代？是什么材质？":"试试：我想看看睡莲"} value={query} onChange={e=>setQuery(e.target.value)}/><div className="composer-actions"><input ref={input} hidden type="file" accept="image/jpeg,image/png,image/webp" onChange={e=>{selectPhoto(e.target.files?.[0]);e.target.value="";}}/><button type="button" className="quiet" onClick={()=>input.current?.click()}>照片预览</button><button className="send-button" disabled={busy||!query.trim()}>发送</button></div></form>
      {error&&<p role="alert">{error}</p>}<p className="composer-note">{publicAiUrl?"限量 AI 体验 · 失败可看预置讲解 · 朗读使用浏览器能力":"静态演示 · 仅预置问题有回答 · 朗读使用浏览器能力"}</p>
    </div></footer>
  </main>;
}
