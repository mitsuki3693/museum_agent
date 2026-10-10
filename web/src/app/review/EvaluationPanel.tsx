"use client";
import {useState} from "react";
import s from "./review.module.css";

type Metric={group:string;variant:string;label:string;value:number|null;unit:string;numerator?:number|null;denominator?:number|null;samples?:number|null};
export type EvaluationRun={_id:string;created_at:number;track:string;status:string;dataset_version:string;dataset_hash:string;corpus_hash:string;model:string;prompt_version:string;embedding_model?:string;visual_index_hash?:string;scope:string;decision:string;human_reviewed:boolean;human_grades_completed:number;result_count:number;metrics:Metric[]};
export type EvaluationData={total:number;shown:number;truncated:boolean;tracks:{id:string;label:string;count:number}[];runs:EvaluationRun[]};
const decisions:Record<string,string>={adopt:"已记录采用决定",reject:"不采用该实验方案",pending:"采用决定未记录"};
export function metricValue(m:Metric){
 if(m.value==null)return "未测／未评分";
 if(m.denominator!=null&&m.numerator!=null)return `${m.numerator} / ${m.denominator}（${(m.value*100).toFixed(1)}%）`;
 if(m.unit==="ratio")return `${(m.value*100).toFixed(1)}%`;
 if(m.unit==="ms")return `${m.value.toLocaleString("zh-CN",{maximumFractionDigits:1})} ms`;
 if(m.unit==="seconds")return `${m.value.toLocaleString("zh-CN",{maximumFractionDigits:3})} 秒`;
 return m.value.toLocaleString("zh-CN",{maximumFractionDigits:4});
}
export default function EvaluationPanel({data}:{data:EvaluationData}){
 const [track,setTrack]=useState("");const [reviewed,setReviewed]=useState("");
 const names=Object.fromEntries(data.tracks.map(t=>[t.id,t.label]));
 const runs=data.runs.filter(r=>(!track||r.track===track)&&(!reviewed||r.human_reviewed===(reviewed==="yes")));
 const missing=data.tracks.filter(t=>t.count===0);
 return <section id="evaluations" className={s.evaluationPanel} aria-labelledby="evaluation-heading">
  <h2 id="evaluation-heading">分层评测</h2>
  <p className={s.intro}>每个批次独立保留题集、语料和模型版本。执行完成、人工复核与方案采用是三件不同的事。</p>
  <p className={s.coverage}>已载入 {data.shown} / {data.total} 个批次。{data.truncated&&"这里只展示最近 200 批，覆盖数量仅针对当前窗口。"} 不合并不同题集的分数，不代表线上 A/B 或生产验收。</p>
  {missing.length>0&&<p className={s.gaps}>待补评测：{missing.map(t=>t.label).join("、")}。当前窗口未归档固定业务评测批次；演示功能和单元测试不等于业务验收。</p>}
  <div className={s.evalFilters}>
   <label>评测层<select aria-label="评测层" value={track} onChange={e=>setTrack(e.target.value)}><option value="">全部层</option>{data.tracks.map(t=><option key={t.id} value={t.id}>{t.label}（{t.count} 批）</option>)}{data.runs.some(r=>r.track==="unclassified")&&<option value="unclassified">未分类</option>}</select></label>
   <label>题集复核<select aria-label="题集复核" value={reviewed} onChange={e=>setReviewed(e.target.value)}><option value="">全部状态</option><option value="no">待人工复核</option><option value="yes">题集已复核</option></select></label>
   <p role="status">显示 {runs.length} 个批次</p>
  </div>
  {runs.length===0&&<div className={s.empty}><h3>尚无符合条件的评测批次</h3><p>调整筛选，或完成该层固定题集并归档结果。此处不会用模拟分数填补空缺。</p></div>}
  {runs.map(run=><details className={s.evalRun} key={run._id}>
   <summary><span className={s.evalRunTitle}>{names[run.track]||"未分类"} · {run.dataset_version}</span><span className={s.evalRunState}>{run.status==="completed"?"执行完成":"执行失败"} · {run.human_reviewed?"题集已复核":"待人工复核"} · {decisions[run.decision]||"采用决定未记录"}</span><span className={s.evalRunState}>批次 {run._id}</span></summary>
   <p className={s.scope}>{run.scope}</p>
   <p className={s.meta}>归档于 {new Date(run.created_at*1000).toLocaleString("zh-CN",{hour12:false})} · 本批次 {run.result_count} 条结果 · 逐条人工评分 {run.human_grades_completed}/{run.result_count}。题集复核不代表所有回答已评分，采用决定不代表当前已部署。</p>
   {run.metrics.length>0?<div className={s.metricScroll} tabIndex={0} role="region" aria-label={`${run.dataset_version} 指标表`}><table className={s.metricTable}>
    <caption>本批次指标；分片、重复执行与不同样本组分别展示</caption>
    <thead><tr><th scope="col">样本组</th><th scope="col">方案</th><th scope="col">指标</th><th scope="col">结果</th></tr></thead>
    <tbody>{run.metrics.map((m,i)=><tr key={`${m.group}:${m.variant}:${m.label}:${i}`}><th scope="row">{m.group}</th><td>{m.variant}</td><td>{m.label}</td><td>{metricValue(m)}{m.samples!=null&&<small>样本数 {m.samples}</small>}</td></tr>)}</tbody>
   </table></div>:<p className={s.scope}>此批次尚无已适配的结构化指标。保留原记录，不推算准确率。</p>}
   <details className={s.versions}><summary>查看版本与复现标识</summary><dl>{[
    ["批次",run._id],["题集 SHA256",run.dataset_hash],["语料 SHA256",run.corpus_hash],
    ["模型",run.model],["提示词版本",run.prompt_version],["向量模型",run.embedding_model],["视觉索引",run.visual_index_hash]
   ].map(([label,value])=><div key={label}><dt>{label}</dt><dd>{value||"未记录"}</dd></div>)}</dl></details>
  </details>)}
 </section>;
}
