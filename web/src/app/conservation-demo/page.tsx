"use client";
import {useState} from "react";
import {initialNote, questions, reminder, samples, sources} from "@/lib/conservation-demo";
import s from "./page.module.css";

type Answer = {question:string; answer:string; status:string; sourceIds:readonly string[]};
type Record = {note:string; createdAt:string; sources:string[]};

function Trend({metric}:{metric:"rh"|"temperature"}) {
  const [low,high] = metric === "rh" ? [45,75] : [20,24];
  const y = (value:number) => 180 - (value-low)/(high-low)*140;
  const coords = samples.map((sample,i) => `${54+i*61},${y(sample[metric])}`);
  return <svg className={s.chart} viewBox="0 0 570 225" role="img" aria-label={metric === "rh" ? "模拟湿度趋势，09点至11点从53%升至67%，提醒线65%" : "模拟温度趋势，09点至11点从21.0升至22.1摄氏度"}>
    {[low,(low+high)/2,high].map(value=><g key={value}><line x1="54" x2="542" y1={y(value)} y2={y(value)} className={s.gridLine}/><text x="43" y={y(value)+4} textAnchor="end">{value}</text></g>)}
    {metric === "rh" && <g><line x1="54" x2="542" y1={y(reminder)} y2={y(reminder)} className={s.reminder}/><text x="540" y={y(reminder)-9} textAnchor="end" className={s.reminderLabel}>演示提醒线 65%</text></g>}
    <polygon points={`54,180 ${coords.join(" ")} 542,180`} className={s.chartFill}/>
    <polyline points={coords.join(" ")} className={s.trendLine}/>
    {samples.map((sample,i)=><circle key={sample.time} cx={54+i*61} cy={y(sample[metric])} r={i===8?5:3} className={s.trendDot}/>)}
    {samples.filter((_,i)=>i%2===0).map((sample,i)=><text key={sample.time} x={54+i*122} y="208" textAnchor="middle">{sample.time}</text>)}
  </svg>;
}

export default function ConservationDemo() {
  const [metric,setMetric] = useState<"rh"|"temperature">("rh");
  const [query,setQuery] = useState(""), [answer,setAnswer] = useState<Answer|null>(null);
  const [evidenceId,setEvidenceId] = useState<string|null>(null), [sourceRead,setSourceRead] = useState(false);
  const [ack,setAck] = useState(false), [note,setNote] = useState(initialNote), [record,setRecord] = useState<Record|null>(null);
  const source = sources.find(item=>item.id===evidenceId);
  function ask(text:string) {
    const normalized=(value:string)=>value.replace(/[？?。\s]/g,"");
    const found=questions.find(item=>normalized(item.question)===normalized(text));
    setAnswer(found || {question:text,answer:"这个演示只预置了本页三个案例问题，尚未连接文保知识检索服务。请点选一个示例问题继续体验。",status:"超出演示范围",sourceIds:[]});
    setQuery("");
  }
  function openSource(id:string) {setEvidenceId(id);setSourceRead(true);}
  function reset() {setMetric("rh");setQuery("");setAnswer(null);setEvidenceId(null);setSourceRead(false);setAck(false);setNote(initialNote);setRecord(null);}
  function saveRecord() {
    if (!sourceRead || !ack || !note.trim() || record) return;
    setRecord({note:note.trim(),createdAt:new Date().toLocaleTimeString("zh-CN",{hour12:false}),sources:sources.map(item=>`${item.id} · ${item.version}`)});
  }
  function exportRecord() {
    if (!record) return;
    const content=["# MUSE Care · 模拟复核记录", "", "记录编号：DEMO-C03-001", "全部读数与处理过程为演示，不是真实文保工单。", "状态：已记录，待复测（不代表风险解除）", `记录时间：${record.createdAt}`, "模拟监测点：示范展区 A / 展柜 C-03", "最新模拟读数：67% RH；22.1°C", "", "## 复核意见",record.note,"", "## 参考资料",...sources.map(item=>`- ${item.id} ${item.title} / ${item.version}\n  ${item.url || "项目编写的模拟规则，无馆方效力"}\n  ${item.limitation}`)].join("\n");
    const url=URL.createObjectURL(new Blob([content],{type:"text/markdown;charset=utf-8"}));
    const link=document.createElement("a");link.href=url;link.download="MUSE-Care-DEMO-C03-001.md";link.click();window.setTimeout(()=>URL.revokeObjectURL(url),1000);
  }
  return <main className={s.page}>
    <header className={s.header}><a className={s.brand} href="/">MUSE.<span>CARE</span></a><span className={s.role}>文保人员 · 演示角色</span><a href="/staff-demo">馆方协作 ↗</a></header>
    <div className={s.workspace}>
      <nav className={s.nav} aria-label="文保工作台导航"><span className={s.navTitle}>CONSERVATION</span><a className={s.navActive} href="#environment">环境与提醒</a><a href="#assistant">文保知识助手</a><a href="#review">人工复核</a><a href="#records">复核记录{record && <span>1</span>}</a><div className={s.navFoot}>独立模拟工作台<br/>公开演示 · 未连接设备<br/><a href="/">返回游客端 ↗</a></div></nav>
      <div className={s.content}>
        <div className={s.heading}><div><span className={s.eyebrow}>COLLECTION CARE / 预防性保护</span><h1>让每一次判断，都有依据。</h1><p>关注环境变化，核对资料，再记录下一步。</p></div><button className="quiet" onClick={reset}>重置演示</button></div>
        <div className={s.banner}><strong>模拟展示</strong><span>环境与藏品信息为虚构案例；问答为预置内容。未连接真实监测、内部资料或设备控制。</span></div>
        <div className={s.stats}><div><span>模拟监测点</span><strong>03<small> 个</small></strong><p>展区环境与展柜</p></div><div className={s.attention}><span>{record?"待复测事项":"待人工复核"}</span><strong>01<small> 条</small></strong><p>湿度提醒 · C-03</p></div><div><span>本轮复核记录</span><strong>{record?"01":"00"}<small> 条</small></strong><p>仅保存在当前页面</p></div></div>
        <div className={s.workGrid}>
          <section id="environment" className={s.panel} aria-label="环境监测演示">
            <div className={s.panelHead}><h2>环境与提醒</h2><span className={s.time}>案例时点 11:00</span></div>
            <div className={s.location}><span>C-03</span><div><strong>示范展区 A · 展柜环境</strong><p>纸本藏品（虚构） · 具体材质与展柜条件待补充</p></div></div>
            <div className={s.readings}><div><span>相对湿度</span><strong>67<small>% RH</small></strong></div><div><span>温度</span><strong>22.1<small>°C</small></strong></div><span className={s.alertBadge}>{record?"已记录 · 待复测":"需要复核"}</span></div>
            <div className={s.chartTabs} aria-label="切换环境趋势"><button aria-pressed={metric==="rh"} onClick={()=>setMetric("rh")}>湿度趋势</button><button aria-pressed={metric==="temperature"} onClick={()=>setMetric("temperature")}>温度趋势</button><span>09:00—11:00 · 模拟采样</span></div>
            <Trend metric={metric}/>
            <p className={s.chartNote}>最近 4 次湿度采样超过演示提醒线。65% RH 是本案例设定，不是馆方标准或通用文保阈值。</p>
            <details className={s.sampleDetails}><summary>查看模拟采样记录与其他点位</summary><div className={s.tableWrap}><table><caption>C-03 模拟采样，每 15 分钟一条</caption><thead><tr><th>时间</th><th>湿度 % RH</th><th>温度 °C</th></tr></thead><tbody>{samples.map(item=><tr key={item.time}><td>{item.time}</td><td>{item.rh}</td><td>{item.temperature.toFixed(1)}</td></tr>)}</tbody></table></div><p>C-01：52% RH / 21.0°C；C-02：54% RH / 21.3°C。均为模拟对照点，本轮未触发演示提醒。</p></details>
          </section>
          <section id="assistant" className={`${s.panel} ${s.assistant}`} aria-label="文保知识助手演示">
            <div className={s.panelHead}><h2>文保知识助手</h2><span className={s.preset}>预置问答</span></div>
            <div className={s.context}>已关联：C-03 · 湿度提醒<br/><span>本次展示证据、适用条件与回答边界。</span></div>
            <div className={s.questionList}>{questions.map(item=><button key={item.id} onClick={()=>ask(item.question)}>{item.question}<span>↗</span></button>)}</div>
            <form className={s.askForm} onSubmit={event=>{event.preventDefault();if(query.trim())ask(query.trim());}}><label htmlFor="conservation-question">输入案例问题</label><div><input id="conservation-question" value={query} onChange={event=>setQuery(event.target.value)} maxLength={200} placeholder="也可以输入上面的示例问题"/><button disabled={!query.trim()} type="submit" aria-label="发送文保问题">↑</button></div></form>
            {answer ? <div className={s.answer} aria-live="polite"><p className={s.asked}>{answer.question}</p><span className={s.answerStatus}>{answer.status}</span><p>{answer.answer}</p><div className={s.citations}>{answer.sourceIds.map(id=><button key={id} onClick={()=>openSource(id)}>查看依据 {id} ↗</button>)}</div></div> : <div className={s.answerEmpty}><span>“</span><p>读数触发提醒之后，<br/>先确认它能够说明什么。</p><small>点选一个问题，查看带依据的示例回答。</small></div>}
          </section>
        </div>
        <section className={s.panel} aria-label="依据与适用范围">
          <div className={s.panelHead}><h2>依据与适用范围</h2><span className={s.time}>1 份模拟规则 · 1 份公开指南</span></div>
          <div className={s.sourceGrid}>{sources.map(item=><button className={s.sourceCard} key={item.id} aria-expanded={evidenceId===item.id} onClick={()=>openSource(item.id)}><span className={s.sourceId}>{item.id}</span><div><strong>{item.title}</strong><small>{item.kind} · {item.version}</small><span>{item.scope}</span></div><span>↗</span></button>)}</div>
          {source && <article className={s.sourceDetail} aria-label={`依据 ${source.id} 详情`}><div className={s.panelHead}><strong>{source.id} · {source.title}</strong><button className="quiet" onClick={()=>setEvidenceId(null)}>收起详情</button></div><p className={s.time}>{source.section}</p><p>{source.text}</p><p className={s.limitation}>{source.limitation}</p>{source.url && <a href={source.url} target="_blank" rel="noreferrer">查看发布机构原文 ↗</a>}</article>}
        </section>
        <section id="review" className={s.panel} aria-label="人工复核演示">
          <div className={s.panelHead}><h2>人工复核</h2><span className={s.preset}>DEMO-C03-001</span></div>
          <div className={s.reviewGrid}><div><p>把待确认的信息与后续动作写清楚。</p><ul className={s.checklist}><li>读数与传感器状态是否可靠？</li><li>藏品材料、状况与展柜条件是否齐全？</li><li>所用规则是否适用于本对象？</li></ul><p className={s.chartNote}>保存后进入“待复测”，不会自动解除提醒。</p></div><div><label className={s.noteLabel} htmlFor="conservation-note">复核意见（可编辑）</label><textarea id="conservation-note" value={note} onChange={event=>setNote(event.target.value)} maxLength={600} rows={4} disabled={!!record}/><label className={s.ack}><input type="checkbox" checked={ack} onChange={event=>setAck(event.target.checked)} disabled={!!record || !sourceRead}/>我已查看依据及适用限制，确认记录此模拟意见</label><button className="primary" onClick={saveRecord} disabled={!sourceRead || !ack || !note.trim() || !!record}>{record?"已记录，等待复测":"保存模拟复核记录"}</button>{!sourceRead && <small className={s.help}>先打开上方任一依据详情，再确认记录。</small>}</div></div>
        </section>
        <section id="records" className={s.panel} aria-label="模拟复核记录">
          <div className={s.panelHead}><h2>复核记录</h2>{record && <button className="quiet" onClick={exportRecord}>导出模拟记录</button>}</div>
          {record ? <div className={s.record} role="status"><span className={s.recordDot}/><div><strong>已记录 · 待复测</strong><small>{record.createdAt} · 演示角色 · DEMO-C03-001</small><p>{record.note}</p><p className={s.chartNote}>依据快照：{record.sources.join("；")}。当前没有新的复测数据。</p></div></div> : <div className={s.noRecord}>本轮还没有复核记录。完成上方复核后，记录会出现在这里。</div>}
        </section>
        <footer className={s.footer}>本演示不保存到服务器，刷新或重置会清空记录。角色标记不代表真实登录权限；正式内部资料仍需身份认证与服务端授权。<a href="/staff-demo">查看客流分流案例 ↗</a></footer>
      </div>
    </div>
  </main>;
}
