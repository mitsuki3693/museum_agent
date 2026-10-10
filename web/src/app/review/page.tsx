"use client";
import {useEffect,useState} from "react";
import s from "./review.module.css";
import EvaluationPanel,{type EvaluationData} from "./EvaluationPanel";
type Review = {status:string;stage:string|null;notes?:string;regression_trace_id?:string};
type Trace = {_id:string;session_id:string;created_at:number;status:string;query?:string;failure_stage?:string;review:Review;result?:{answer?:string;latency_ms?:number};latency_ms?:number;[key:string]:unknown};
type Feedback = {trace_id:string;kind:string;comment?:string};
type Cursor = {before:number;before_id:string};
type Data = {next_cursor:Cursor|null;traces:Trace[];photo_traces:Trace[];feedback:Feedback[]};
type Ops = {storage:string;daily_backup:boolean;backup:{status:string;completed_at?:number};eval_runs:{_id:string;dataset_version:string;human_reviewed:boolean;status:string;results:unknown[];human_grades_completed?:number;summary:unknown}[]};
const stages:Record<string,string>={retrieval:"资料检索",vision:"图片识别",generation:"回答生成",verification:"依据核对",service:"服务故障"};
const outcomes:Record<string,string>={answered:"已回答",needs_confirmation:"待游客确认",not_matched:"未识别",insufficient_evidence:"资料不足",verification_failed:"核对未通过",service_unavailable:"服务不可用",timeout:"超时",invalid_image:"图片无效",retrieval_only:"仅检索",route_ready:"路线已生成",running:"处理中",interrupted:"重启前已中断"};
const reviews:Record<string,string>={pending:"待复盘",confirmed:"已确认问题",fixed:"已修复待跟踪",not_a_bug:"符合预期"};
const feedbackLabels:Record<string,string>={helpful:"有帮助",wrong_fact:"事实有误",not_answered:"没有解答"};
function TraceRow({trace,kind,feedback,save,disabled}:{trace:Trace;kind:"text"|"photo";feedback:Feedback[];save:(id:string,kind:string,review:Review)=>Promise<boolean>;disabled:boolean}){
 const [review,setReview]=useState<Review>(trace.review||{status:"pending",stage:null});
 const [saved,setSaved]=useState(false);
 return <article className={s.record}><div className={s.recordHead}><span>{kind==="photo"?"照片":"对话"}</span><time>{new Date(trace.created_at*1000).toLocaleString("zh-CN",{hour12:false})}</time><span>{outcomes[trace.status]||trace.status}</span></div>
  <h2>{kind==="photo"?"照片识别记录":trace.query||"未完成的请求"}</h2>{trace.result?.answer&&<p className={s.answer}>{trace.result.answer}</p>}
  <p className={s.meta}>编号 {trace._id} · {trace.result?.latency_ms??trace.latency_ms??"—"} ms</p>
  {feedback.map(f=><p className={s.feedback} key={f.trace_id}>游客反馈：{feedbackLabels[f.kind]||f.kind}{f.comment&&` · ${f.comment}`}</p>)}
  <details className={s.details}><summary>查看候选、引用、生成过程与版本</summary><pre>{JSON.stringify(trace,null,2)}</pre></details>
  <details className={s.details}><summary>人工复盘 · {reviews[trace.review?.status]||"待复盘"}</summary><div className={s.fields}>
   <label>处理状态<select value={review.status} onChange={e=>{setReview({...review,status:e.target.value});setSaved(false);}}>{Object.entries(reviews).map(([k,v])=><option key={k} value={k}>{v}</option>)}</select></label>
   <label>问题发生环节<select value={review.stage||""} onChange={e=>{setReview({...review,stage:e.target.value||null});setSaved(false);}}><option value="">尚未确定</option>{Object.entries(stages).map(([k,v])=><option value={k} key={k}>{v}</option>)}</select></label></div>
   <label>原因、改动与判断依据<textarea maxLength={1000} value={review.notes||""} onChange={e=>{setReview({...review,notes:e.target.value});setSaved(false);}}/></label>
   <label>关联回归记录编号（可选）<input value={review.regression_trace_id||""} onChange={e=>{setReview({...review,regression_trace_id:e.target.value});setSaved(false);}}/></label>
   <button disabled={disabled} onClick={async()=>setSaved(await save(trace._id,kind,{...review,regression_trace_id:review.regression_trace_id||undefined}))}>保存复盘</button>{saved&&<span role="status"> 已保存</span>}
  </details></article>;
}
export default function ReviewPage(){
 const [token,setToken]=useState("");const [data,setData]=useState<Data|null>(null);const [ops,setOps]=useState<Ops|null>(null);
 const [evaluations,setEvaluations]=useState<EvaluationData|null>(null);const [view,setView]=useState("traces");
 useEffect(()=>{if(window.location.hash==="#evaluations")setView("evaluations");},[]);
 const [error,setError]=useState("");const [busy,setBusy]=useState(false);const [stage,setStage]=useState("");const [status,setStatus]=useState("");const [session,setSession]=useState("");const [notice,setNotice]=useState("");const [before,setBefore]=useState<Cursor|undefined>();
 async function call(path:string,init:RequestInit={}){const r=await fetch(`/api/museum/admin/${path}`,{...init,headers:{"Content-Type":"application/json",Authorization:`Bearer ${token}`,...init.headers}});const d=await r.json();if(!r.ok)throw new Error(typeof d.detail==="string"?d.detail:"请求未完成，请检查输入后重试");return d;}
 function query(cursor?:Cursor){const p=new URLSearchParams();if(stage)p.set("stage",stage);if(status)p.set("status",status);if(session)p.set("session_id",session);if(cursor){p.set("before",String(cursor.before));p.set("before_id",cursor.before_id);}return p.toString();}
 async function load(cursor?:Cursor){setBusy(true);setError("");setNotice("");try{const [d,o,e]=await Promise.all([call(`traces?${query(cursor)}`),call("operations"),call("evaluations")]);setData(d);setOps(o);setEvaluations(e);setBefore(cursor);}catch(e){setData(null);setOps(null);setEvaluations(null);setError((e as Error).message);}finally{setBusy(false);}}
 async function save(id:string,kind:string,review:Review){setBusy(true);setError("");try{await call(`reviews/${kind}/${id}`,{method:"POST",body:JSON.stringify(review)});await load(before);return true;}catch(e){setError((e as Error).message);return false;}finally{setBusy(false);}}
 async function exportData(){setBusy(true);setError("");try{const d=await call(`export?${query(before)}`);const url=URL.createObjectURL(new Blob([JSON.stringify(d,null,2)],{type:"application/json"}));const a=document.createElement("a");a.href=url;a.download=`museum-metrics-${Date.now()}.json`;a.click();URL.revokeObjectURL(url);setNotice("已导出当前筛选与时间范围的指标（最多500条）；不含问题、回答、备注和照片。");}catch(e){setError((e as Error).message);}finally{setBusy(false);}}
 async function backup(){setBusy(true);setError("");try{await call("backup",{method:"POST"});await load(before);setNotice("备份完成，已保存到本机私有备份目录。");}catch(e){setError((e as Error).message);}finally{setBusy(false);}}
 const rows=data?[...data.traces.map(trace=>({trace,kind:"text" as const})),...data.photo_traces.map(trace=>({trace,kind:"photo" as const}))].sort((a,b)=>b.trace.created_at-a.trace.created_at):[];
 return <main className={s.page}><header className={s.header}><a href="/" className={s.brand}>MUSE.</a><a href="/">返回游客端</a></header><h1>回答与识别复盘</h1><p className={s.intro}>从失败记录找到下一步改进。自动分类只是线索，事实与原因由人工确认。</p>
  <form className={s.access} onSubmit={e=>{e.preventDefault();void load();}}><label>管理凭据<input type="password" autoComplete="off" value={token} onChange={e=>setToken(e.target.value)} placeholder="使用本机配置的管理凭据" required/></label><button disabled={busy||!token} type="submit">{busy?"处理中…":"载入记录"}</button><span>凭据仅留在当前页面，刷新即清除。</span></form>
  {error&&<p role="alert" className={s.error}>{error}</p>}{notice&&<p role="status" className={s.notice}>{notice}</p>}
  {data&&<><section className={s.operations} aria-label="数据保存状态"><div><strong>{ops?.storage==="mongo"?"MongoDB 持久保存":"内存模式 · 重启会丢失记录"}</strong><p>每日备份：{ops?.daily_backup?"已开启（服务运行时）":"未开启"} · 最近结果：{({completed:"已完成",failed:"失败",pending:"待执行",disabled:"未配置"} as Record<string,string>)[ops?.backup.status||""]||"未知"}{ops?.backup.completed_at&&` · ${new Date(ops.backup.completed_at*1000).toLocaleString("zh-CN")}`}</p></div><button disabled={busy||ops?.storage!=="mongo"} onClick={backup}>立即备份</button></section>
   <nav className={s.viewSwitch} aria-label="复盘视图"><button aria-pressed={view==="traces"} onClick={()=>{setView("traces");history.replaceState(null,"","#traces");}}>运行复盘</button><button aria-pressed={view==="evaluations"} onClick={()=>{setView("evaluations");history.replaceState(null,"","#evaluations");}}>分层评测</button></nav>
   {view==="evaluations"&&evaluations?<EvaluationPanel data={evaluations}/>:<><form className={s.filters} onSubmit={e=>{e.preventDefault();void load();}}><label>失败环节<select value={stage} onChange={e=>setStage(e.target.value)}><option value="">全部环节</option>{Object.entries(stages).map(([k,v])=><option key={k} value={k}>{v}</option>)}</select></label><label>执行结果<select value={status} onChange={e=>setStatus(e.target.value)}><option value="">全部结果</option>{Object.entries(outcomes).map(([k,v])=><option key={k} value={k}>{v}</option>)}</select></label><label>会话编号<input value={session} onChange={e=>setSession(e.target.value)} placeholder="可留空"/></label><button disabled={busy}>筛选</button></form>
   <div className={s.listHead}><p>本页 {rows.length} 条记录 · {data.feedback.length} 条关联反馈</p><button disabled={busy} onClick={exportData}>导出脱敏指标</button></div>
   {rows.length===0?<div className={s.empty}><h2>没有符合条件的记录</h2><p>可以清除筛选，或在游客端完成一次问答、识图后再载入。</p></div>:rows.map(({trace,kind})=><TraceRow key={trace._id} trace={trace} kind={kind} feedback={data.feedback.filter(f=>f.trace_id===trace._id)} save={save} disabled={busy}/>)}
   <nav className={s.paging} aria-label="记录翻页"><button disabled={busy||!before} onClick={()=>load()}>回到最新</button><button disabled={busy||!data.next_cursor} onClick={()=>load(data.next_cursor||undefined)}>查看更早记录</button></nav>
   <section className={s.evals}><h2>历史批次原始摘要</h2><p>结构化指标见上方“分层评测”；这里保留最近批次的原始记录。</p>{!ops?.eval_runs.length&&<p>尚无保存的评测批次。</p>}{ops?.eval_runs.map(run=><details key={run._id}><summary>{run.dataset_version} · {run.status==="completed"?"执行完成":"执行失败"} · {run.human_reviewed?"题集已复核":"题集待复核"} · 回答人工评分 {run.human_grades_completed||0}/{run.results.length}</summary><pre>{JSON.stringify(run.summary,null,2)}</pre></details>)}</section></>}</>}
 </main>;
}
