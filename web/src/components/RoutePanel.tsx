"use client";
import {useId, useState} from "react";
import {FacilityResult, RouteOptions, RoutePreferences, VisitRoute} from "@/lib/route-types";
import RouteMapDemo from "./RouteMapDemo";

export default function RoutePanel({options, initial, route, facilities, busy, onPlan, onChoose, onFind}: {
  options: RouteOptions; initial?: RoutePreferences; route?: VisitRoute; facilities?: FacilityResult; busy: boolean;
  onPlan: (preferences: RoutePreferences) => void; onChoose: (id: string) => void; onFind: (query: string) => void;
}) {
  const [preferences, setPreferences] = useState<RoutePreferences>(initial || route?.preferences || {
    minutes: 30, start_id: options.default_start || "", interests: [], step_free: false, skip_ids: [],
  });
  const inputId = useId();
  const starts = options.starts || [], interests = options.interests || [];
  function update(patch: Partial<RoutePreferences>) {setPreferences(current => ({...current, ...patch}));}
  return <div className="route-panel">
    {options.venue && <div className="route-heading"><span>路线与设施{options.synthetic ? " · 模拟资料" : ""}</span><strong>{options.venue}</strong><p>{options.scope}</p></div>}
    <nav className="route-chips facility-shortcuts" aria-label="找常用设施">{["出口", "楼梯／电梯", "厕所", "服务台"].map(label => <button type="button" key={label} disabled={busy} onClick={() => onFind(`找${label}`)}>找{label}</button>)}</nav>
    {facilities && <div className="facility-results">{facilities.items.map(item => <article key={item.id}><strong>{item.title}</strong><p>{item.location}</p><p className="route-time-note">{item.note}</p><a href={item.map_url} target="_blank" rel="noreferrer">查看馆方位置 ↗</a></article>)}
      {facilities.sources.length > 0 && <details className="route-directions"><summary>设施资料来源</summary>{facilities.sources.map(source => <p key={source.id}><a href={source.url} target="_blank" rel="noreferrer">{source.title} ↗</a></p>)}</details>}
      <button className="quiet" disabled={busy} onClick={() => onFind("帮我规划参观路线")}>继续安排参观路线</button>
    </div>}
    {route && <>
      <RouteMapDemo route={route}/>
      <div className="route-metrics"><strong>约 {route.total_minutes} <small>分钟</small></strong><span>{route.steps.length} 个展厅 · 停留 {route.view_minutes} 分钟<br/>移动 {route.walk_minutes} 分钟 · 余量 {route.remaining_minutes} 分钟</span></div>
      <p className="route-time-note">{route.time_note}</p>
      <p className="route-start">从 <strong>{route.start.title}</strong> 出发 · {route.start.level}</p>
      <ol className="route-stops">{route.steps.map((step, index) => <li key={step.id}>
        <span className="route-step-number" aria-hidden="true">{index + 1}</span>
        <div><div className="route-stop-heading"><strong>{step.title}</strong><small>{step.level}</small></div>
          <p className="route-walk">{step.via.length ? `经 ${step.via.map(via => via.title).join(" → ")} · 移动约 ${step.walk_minutes} 分钟` : "就在你确认的起点，无需跨厅移动。"}</p>
          {step.via.some(via => via.step_free === false) && <p className="route-stairs">这一段包含台阶。</p>}
          <p>{step.description}</p><p className="route-reason">{step.reason} 建议停留 {step.view_minutes} 分钟。</p>
          {step.via.length > 0 && <details className="route-directions"><summary>查看这一段怎么走</summary>{step.via.map((via, i) => <p key={i}>{via.instruction}</p>)}</details>}
          <div className="route-stop-actions">{step.object_id && <button className="quiet" disabled={busy} onClick={() => onChoose(step.object_id!)}>听这件作品的讲解</button>}
            <button className="quiet" aria-label={`跳过${step.title}`} disabled={busy} onClick={() => onPlan({...route.preferences, skip_ids: [...new Set([...route.preferences.skip_ids, step.id])]})}>跳过此站，重新规划</button></div>
        </div>
      </li>)}</ol>
    </>}
    {options.available && !facilities && <details className="route-edit" open={!route}>
      <summary>{route ? "调整时间、兴趣或起点" : "确认你的参观偏好"}</summary>
      <form onSubmit={event => {event.preventDefault(); onPlan(preferences);}}>
        <label htmlFor={`${inputId}-time`}>你有多少时间？<span className="route-time-input"><input id={`${inputId}-time`} type="number" required min={15} max={90} value={preferences.minutes} disabled={busy} onChange={event => update({minutes: Number(event.target.value)})}/> 分钟</span></label>
        <div className="route-chips">{[30, 60, 90].map(minutes => <button type="button" key={minutes} aria-pressed={preferences.minutes === minutes} disabled={busy} onClick={() => update({minutes})}>{minutes} 分钟</button>)}</div>
        <label htmlFor={`${inputId}-start`}>从哪里出发？<select id={`${inputId}-start`} value={preferences.start_id} disabled={busy} onChange={event => update({start_id: event.target.value})}>{starts.map(start => <option key={start.id} value={start.id}>{start.title} · {start.level}</option>)}</select></label>
        <fieldset disabled={busy}><legend>更想看什么？<small>可多选，不选则综合推荐</small></legend><div className="route-chips">{interests.map(interest => <button type="button" key={interest.id} aria-pressed={preferences.interests.includes(interest.id)} onClick={() => update({interests: preferences.interests.includes(interest.id) ? preferences.interests.filter(id => id !== interest.id) : [...preferences.interests, interest.id]})}>{interest.label}</button>)}</div></fieldset>
        <label className="route-accessibility"><input type="checkbox" checked={preferences.step_free} disabled={busy} onChange={event => update({step_free: event.target.checked})}/>需要全程无台阶（轮椅／婴儿车）</label>
        {preferences.skip_ids.length > 0 && <p className="route-skipped">已跳过：{preferences.skip_ids.map(id => starts.find(start => start.id === id)?.title || id).join("、")}。经过这些展厅时仍会计算移动时间。<button type="button" disabled={busy} onClick={() => update({skip_ids: []})}>恢复全部</button></p>}
        <p className="route-time-note">起点由你确认，应用不会自动定位。首版支持 15–90 分钟。</p>
        <button className="primary" type="submit" disabled={busy || !preferences.start_id || preferences.minutes < 15 || preferences.minutes > 90}>{busy ? "规划中…" : "生成参观路线"}</button>
      </form>
    </details>}
    <p className="route-notice">{options.notice}</p>
    {options.map_url && <div className="route-source"><a href={options.map_url} target="_blank" rel="noreferrer">打开馆方地图 ↗</a><span>资料核对：{options.checked_at}</span></div>}
    {route && <details className="route-directions"><summary>路线资料来源</summary>{route.sources.map(source => <p key={source.id}><a href={source.url} target="_blank" rel="noreferrer">{source.title} ↗</a></p>)}</details>}
  </div>;
}
