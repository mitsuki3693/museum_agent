"use client";
import { useEffect, useState, useRef } from "react";
import MuseumPhoto, {MuseumObject} from "@/components/MuseumPhoto";
import {VoiceInput, ListenButton} from "@/components/MuseumSpeech";
import {createRequestId} from "@/lib/request-id";
import ArtworkCandidate from "@/components/ArtworkCandidate";

type Source = {id:string; title:string; content:string; source_url:string; fetched_at:string; license:string;attribution?:string;license_url?:string;source_kind?:string;narrator?:string};
type Result = {narration?:{prepared:boolean;human_reviewed:boolean;version:string};trace_id:string; status:string; answer:string; mode:string;candidates?:{id:string;title:string}[]; claims:{text:string;source_id:string;quote:string}[]; sources:Source[]; latency_ms:number};
type Item = MuseumObject;
type Turn = {question:string;result:Result};
type Health = {model_configured:boolean;storage:string;retrieval:string;corpus_count:number};
const labels:Record<string,string>={needs_confirmation:"先确认是哪件作品",answered:"已核对引用依据",retrieval_only:"原始资料",insufficient_evidence:"资料不足",verification_failed:"讲解尚未通过核对",service_unavailable:"服务暂不可用"};
async function api(path:string, options:RequestInit={}) {
 const response=await fetch(`/api/museum/${path}`,options);
 if(!response.headers.get("content-type")?.includes("application/json")) throw new Error("暂时连接不上服务，请稍后重试。");
 const data=await response.json();
 if(!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "请求未完成，请检查输入后重试。");
 return data;
}
export default function Home(){
 const [health,setHealth]=useState<Health|null>(null),[items,setItems]=useState<Item[]>([]);
 const [selected,setSelected]=useState(""),[query,setQuery]=useState("");
 const [turns,setTurns]=useState<Turn[]>([]),[busy,setBusy]=useState(false),[error,setError]=useState("");
 const [filter,setFilter]=useState(""),[feedback,setFeedback]=useState<Record<string,string>>({});
 const token=useRef(""); const end=useRef<HTMLDivElement>(null);
 useEffect(()=>{Promise.all([api("health"),api("objects")]).then(([h,o])=>{setHealth(h);setItems(o);}).catch(e=>setError(e.message));},[]);
 useEffect(()=>{end.current?.scrollIntoView({behavior:"smooth",block:"end"});},[turns]);
 async function ensureSession(){ if(!token.current) token.current=(await api("sessions",{method:"POST"})).token;return token.current; }
 async function ask(text=query, objectOverride=selected, mode="brief", action="question"){
  if(!text.trim()||busy)return;setBusy(true);setError("");
  try{const t=await ensureSession();const result=await api("chat",{method:"POST",headers:{"Content-Type":"application/json",Authorization:`Bearer ${t}`},body:JSON.stringify({query:text.trim(),object_id:objectOverride,mode,action,request_id:createRequestId()})});setTurns(v=>[...v,{question:text,result}]);setQuery("");}
  catch(e){setError((e as Error).message);}finally{setBusy(false);}
 }
 async function clear(){
  setBusy(true);setError("");try{if(token.current)await api("session",{method:"DELETE",headers:{Authorization:`Bearer ${token.current}`}});token.current="";setTurns([]);setSelected("");setFeedback({});}
  catch(e){setError((e as Error).message);}finally{setBusy(false);}
 }
 async function rate(id:string,kind:string){try{await api("feedback",{method:"POST",headers:{"Content-Type":"application/json",Authorization:`Bearer ${token.current}`},body:JSON.stringify({trace_id:id,kind})});setFeedback(v=>({...v,[id]:kind}));}catch(e){setError((e as Error).message);}}
 function choose(id:string){setSelected(id);ask("请简明讲解这件作品，让我先知道值得留意的地方。",id,"brief","narration");}
 function narrate(id:string,style:string){const labels:Record<string,string>={brief:"简明版",deep:"深入版",children:"儿童版"};setSelected(id);ask(`请用${labels[style]}讲解这件作品。`,id,style,"narration");}
 const selectedItem=items.find(x=>x.id===selected);
 return <main className="museum-shell">
  <header className="topbar"><a className="brand" href="/">馆语<span>MUSEUM NOTES</span></a><span className="edition">拍下眼前的好奇，听见作品的故事</span><button className="quiet" onClick={clear} disabled={busy}>重新开始</button></header>
  <div className="workspace">

   <section className="conversation" aria-label="藏品问答">
    {!turns.length&&<MuseumPhoto configured={Boolean(health?.model_configured)} objects={items} ensureSession={ensureSession} onChoose={choose} disabled={busy}/>}
   <details className="collection browse-panel"><summary>拍照不方便？按作品名称找</summary><div className="eyebrow">THE COLLECTION</div><h2>查找作品</h2><p className="muted">{items.some(i=>i.collection==="V&A")?"V&A 讲解试点 · 芝加哥艺术博物馆示例":"芝加哥艺术博物馆 · 公开馆藏资料"}</p>
    <label className="search-label">查找藏品<input value={filter} onChange={e=>setFilter(e.target.value)} placeholder="输入作品名称" /></label>
    <button className={`object-card ${!selected?"selected":""}`} onClick={()=>setSelected("")} disabled={busy}><span>全部馆藏</span><small>跨藏品检索</small></button>
    <div className="object-list">{items.filter(i=>(i.title+" "+(i.display_title||"")+" "+i.id).toLowerCase().includes(filter.toLowerCase())).map((item,i)=><button key={item.id} disabled={busy} className={`object-card ${selected===item.id?"selected":""}`} onClick={()=>choose(item.id)}><span>{item.display_title||item.title}</span></button>)}</div>
    <p className="footnote">资料为固定快照，不能确认实时展位、开放时间或票价。本演示与馆方无隶属关系。</p>
   </details>
    <div className={`conversation-head ${!turns.length?"conversation-head-idle":""}`}><div><div className="eyebrow">EXPLORE WITH EVIDENCE</div><h1>{selectedItem ? (selectedItem.display_title||selectedItem.title) : "也可以直接问一个问题"}</h1></div></div>
    {health&&!health.model_configured&&<div className="notice">当前仅提供资料检索，AI 回答尚未启用。检索结果保留原文和官方来源。</div>}
    {health?.storage==="memory"&&<p className="session-note">演示会话保留 30 分钟；服务重启后，聊天和反馈会清空。</p>}
    <div className="turns" aria-live="polite">
     {turns.map(({question,result})=><article className="turn" key={result.trace_id}><div className="question">{question}</div><div className="answer"><div className="answer-label">馆语 <span>{labels[result.status]||result.status}</span></div>{result.status==="answered"&&<ListenButton text={result.answer}/>}
      {result.sources.some(s=>s.source_kind==="official_transcript")&&<p className="narration-note">依据馆方英文讲解改写 · {{brief:"简明版",deep:"深入版",children:"儿童版"}[result.mode]||"问答"} · 非馆方官方中文稿{result.narration&&!result.narration.human_reviewed?"，待体验评审":""}</p>}
      {result.claims.length?result.claims.map((claim,i)=><div className="claim" key={i}><p>{claim.text}</p><details><summary>查看这条陈述的原文依据 · {claim.source_id}</summary><blockquote>{claim.quote}</blockquote></details></div>):<p className="answer-text">{result.answer}</p>}
      {result.candidates?.length?<div className="discovery-candidates">{result.candidates.map(c=><ArtworkCandidate key={c.id} work={{...c,...items.find(i=>i.id===c.id)}} disabled={busy} onConfirm={choose}/>)}<button className="quiet" disabled={busy} onClick={()=>{setSelected("");setQuery("");document.getElementById("question")?.focus();}}>都不是，补充描述</button></div>:null}
      {result.status==="answered"&&result.sources.length===1&&<div className="followup-options"><span>换一种讲法</span>{([["brief","简明版"],["deep","深入版"],["children","儿童版"]]).map(([style,label])=><button key={style} aria-pressed={result.mode===style} disabled={busy} onClick={()=>narrate(result.sources[0].id,style)}>{label}</button>)}<span>也可以在下方继续追问</span></div>}
      {result.sources.length>0&&<div className="sources"><h3>{result.status==="answered"?"引用来源":"可查阅的原始资料"}</h3>{result.sources.map(s=><details key={s.id}><summary>{s.source_kind==="official_transcript"?"馆方英文讲解原文":""} {s.title}</summary><p className="source-meta">{s.id} · {s.license} · 采集于 {s.fetched_at.slice(0,10)}</p>{s.narrator&&<p>原讲解者：{s.narrator}</p>}<pre>{s.content}</pre><p className="source-meta">{s.attribution} {s.license_url&&<a href={s.license_url} target="_blank" rel="noreferrer">许可说明</a>}</p><a href={s.source_url} target="_blank" rel="noreferrer">打开馆方原始页面 ↗</a></details>)}</div>}
      <div className="feedback"><span>这次回答有帮助吗？</span>{([["helpful","有帮助"],["wrong_fact","事实有误"],["not_answered","没有解答"]] as string[][]).map(([kind,label])=><button aria-pressed={feedback[result.trace_id]===kind} className={feedback[result.trace_id]===kind?"chosen":""} key={kind} onClick={()=>rate(result.trace_id,kind)}>{label}</button>)}{feedback[result.trace_id]&&<small>已记录</small>}</div>
     </div></article>)}
     {busy&&<p className="loading" role="status">正在查找资料并核对依据…</p>}<div ref={end}/>
    </div>
    <div className="composer-area">{error&&<div role="alert" className="error">{error}</div>}<VoiceInput onText={setQuery} disabled={busy}/><form className="composer" onSubmit={e=>{e.preventDefault();ask();}}><label className="sr-only" htmlFor="question">你的问题</label><textarea id="question" value={query} onChange={e=>setQuery(e.target.value)} placeholder={selectedItem?`关于 ${selectedItem.title}，你想了解什么？`:"不知道名字也没关系，描述你看到的内容…"} maxLength={600} rows={2} disabled={busy}/><button type="submit" disabled={busy||!query.trim()}>{busy?"核对中…":"发送 ↗"}</button></form><p className="composer-note">回答以所列资料为依据；资料不足时会说明。请勿输入个人敏感信息。 <a href="/review">查看评审记录</a></p></div>
   </section>
  </div>
 </main>;
}
