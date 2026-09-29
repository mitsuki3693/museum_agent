"use client";
import {useEffect, useState} from "react";
import {museumApi} from "@/lib/museum-api";
import type {VisitRoute} from "@/lib/route-types";

type Plan = {status: string; answer: string; route?: VisitRoute};
type Case = {
  available: boolean; simulated: true; title: string; affected_id: string; affected_title: string;
  people: number; humidity: number; attention_humidity: number;
  area: [number, number][]; visitors: [number, number][];
  source_title: string; source_url: string; evidence: string[]; before: Plan; after: Plan;
};
type Map = {available: boolean; level: string; attribution: string; source_url: string;
  view_box: [number, number, number, number]; tiles: {id: string; x: number; y: number; size: number; url: string}[]};

export default function StaffDemo() {
  const [data, setData] = useState<Case | null>(null), [map, setMap] = useState<Map | null>(null);
  const [error, setError] = useState(""), [loading, setLoading] = useState(true);
  const [reviewed, setReviewed] = useState(false), [confirmed, setConfirmed] = useState(false);
  const [fullMap, setFullMap] = useState(false), [showEvidence, setShowEvidence] = useState(false);
  const [imageError, setImageError] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    Promise.all([museumApi("demo/operations", {signal: controller.signal}), museumApi("routes/floor-demo", {signal: controller.signal})])
      .then(([scenario, floor]) => {if (!controller.signal.aborted) {setData(scenario); setMap(floor);}})
      .catch(() => {if (!controller.signal.aborted) setError("演示暂时无法加载，请刷新后重试。");})
      .finally(() => {if (!controller.signal.aborted) setLoading(false);});
    return () => controller.abort();
  }, []);
  function reset() {setReviewed(false); setConfirmed(false); setShowEvidence(false);}
  const active = confirmed ? data?.after : data?.before;
  const xs = data?.area?.map(p => p[0]) || [0], ys = data?.area?.map(p => p[1]) || [0];
  const focus = [Math.min(...xs) - 150, Math.min(...ys) - 100, Math.max(...xs) - Math.min(...xs) + 300, Math.max(...ys) - Math.min(...ys) + 200];
  return <main className="ops-shell">
    <header className="ops-header"><a href="/" className="brand"><strong>MUSE.</strong></a><span>馆方工作台 · 案例演示</span><a href="/">返回游客端 ↗</a></header>
    <div className="ops-intro"><span className="ops-eyebrow">ONE SCENARIO / 两端协作</span><h1>从发现异常，到调整参观。</h1>
      <p>模拟游客聚集与环境异常，演示工作人员复核后，游客路线如何变化。</p>
      <div className="ops-demo-notice"><strong>演示数据</strong> 人数、环境读数与限制均为模拟。此页不读取内部文保资料，也不会发布真实通行限制。</div>
    </div>
    {loading && <p role="status">正在加载演示案例…</p>}
    {error && <p role="alert">{error}</p>}
    {!loading && !error && (!data?.available || !map?.available) && <p>本地尚未配置此案例与真实地图，请回到游客端继续体验。</p>}
    {data?.available && map?.available && <>
      <ol className="ops-steps" aria-label="案例进度">{["发现异常", "复核依据", "确认措施", "游客路线调整"].map((title, i) => <li key={title} className={(confirmed ? i <= 3 : reviewed ? i <= 1 : i === 0) ? "active" : ""}><span>{String(i + 1).padStart(2,"0")}</span>{title}</li>)}</ol>
      <div className="ops-layout">
        <section className="ops-card ops-map-card" aria-label="模拟展厅热区">
          <div className="ops-card-heading"><h2>展厅聚集情况</h2><button className="quiet" onClick={() => setFullMap(v => !v)}>{fullMap ? "聚焦异常区域" : "查看楼层全图"}</button></div>
          <div className="ops-map-frame">
            <svg className="ops-map" viewBox={(fullMap ? map.view_box : focus).join(" ")} role="img" aria-label="真实底图上的模拟聚集区域，非实时客流">
              {map.tiles.map(tile => <image key={tile.id} href={tile.url} x={tile.x} y={tile.y} width={tile.size} height={tile.size} onError={() => setImageError(true)}/>)}
              {!imageError && <>
                <polygon points={data.area.map(p => p.join(",")).join(" ")} fill={confirmed ? "#7646bd66" : "#f3784b77"} stroke={confirmed ? "#6b3194" : "#bd4020"} strokeWidth="4"/>
                {data.visitors.map(([x,y],i) => <circle key={i} cx={x} cy={y} r="5" fill="#461827" stroke="#fff" strokeWidth="1.5"/>)}
              </>}
            </svg>
            <span className="ops-map-badge">{map.level} · 模拟聚集</span>
            {imageError && <p className="error">底图未加载完整，请刷新后查看。</p>}
          </div>
          <p className="ops-map-legend"><span/>{data.people} 个模拟游客点 · {confirmed ? "该区域已标记临时限制" : "橙色为模拟聚集区域"}</p>
          <p className="ops-subtle">{confirmed ? "这是措施确认前的事件快照，尚无分流后的观测数据。" : "这张图展示案例快照，不能代表实际客流或定位能力。"}</p>
          <a className="map-attribution" href={map.source_url} target="_blank" rel="noreferrer">{map.attribution} · 官方底图 ↗</a>
        </section>
        <section className="ops-card" aria-label="工作人员异常复核">
          <div className="ops-card-heading"><h2>工作人员 · 异常复核</h2><span className={`ops-status ${confirmed ? "confirmed" : ""}`}>{confirmed ? "模拟措施已确认" : "待人工复核"}</span></div>
          <h3>{data.affected_title}</h3>
          <div className="ops-metrics"><div><strong>{data.people}<small> 人</small></strong><span>模拟在厅人数</span></div><div><strong>{data.humidity}<small>% RH</small></strong><span>模拟环境湿度</span></div></div>
          <p className="ops-subtle">本案例提醒线为 {data.attention_humidity}% RH，仅用于演示，不是馆方标准或通用文保阈值。不能据此认定人群造成了湿度异常。</p>
          <div className="ops-action-note"><strong>建议先复核</strong><p>核对读数、藏品材质与展柜条件，并由工作人员判断是否需要临时调整参观。</p></div>
          <button className="quiet" aria-expanded={showEvidence} onClick={() => setShowEvidence(v => !v)}>{showEvidence ? "收起依据" : "查看文保依据"}</button>
          {showEvidence && <div className="ops-evidence"><strong>{data.source_title}</strong><ul>{data.evidence.map(text => <li key={text}>{text}</li>)}</ul><a href={data.source_url} target="_blank" rel="noreferrer">查看公开原文 ↗</a><p>本案例为预置资料摘要，尚未接入内部文保 RAG。</p>
            <label><input type="checkbox" checked={reviewed} disabled={confirmed} onChange={e => setReviewed(e.target.checked)}/>已查看依据与适用限制，继续演示人工决策</label></div>}
          <div className="ops-confirm"><p>演示措施：暂不安排进入该展厅，重新计算其余可达展厅的参观顺序。</p>
            <button className="primary" disabled={!reviewed || confirmed} onClick={() => setConfirmed(true)}>{confirmed ? "已确认 · 查看下方游客结果" : "确认模拟分流措施"}</button>
            {!reviewed && <small>先查看依据并确认，才能执行演示措施。</small>}</div>
        </section>
      </div>
      <section className="ops-card ops-visitor" aria-label="游客路线调整预览">
        <div className="ops-card-heading"><h2>游客端 · 路线预览</h2><span className="ops-status">{confirmed ? "调整后" : "调整前"}</span></div>
        <p>{confirmed ? `模拟通知：${data.affected_title}暂不安排参观，已按原时间与兴趣重新计算。` : "工作人员尚未确认措施，游客路线保持原安排。"}</p>
        {active?.route ? <>
          <div className="ops-route-strip"><span>起点 · {active.route.start.title.split(" · ")[0]}</span>{active.route.steps.map(step => <span key={step.id} className={step.id === data.affected_id ? "affected" : ""}>→ {step.title.split(" · ")[0]}</span>)}</div>
          <p className="ops-subtle">{active.route.preferences.minutes} 分钟预算 · 预计使用 {active.route.total_minutes} 分钟 · 移动与停留时间为项目估计</p>
          {confirmed && <p className="ops-success" role="status">受限展厅已从参观站点和途经通道中排除。</p>}
          <details className="route-directions"><summary>查看各段途经展厅</summary>{active.route.steps.map(step => <p key={step.id}>前往 {step.title.split(" · ")[0]}：{step.via.length ? step.via.map(v => v.title.split(" · ")[0]).join(" → ") : "就在起点"}</p>)}</details>
        </> : <p role="status">{active?.answer || "暂无可用路线。"}</p>}
        <p className="ops-subtle">只更新本页的游客预览，不影响正在使用游客端的人。限制成立不代表已有“客流下降”或“环境改善”的结果。</p>
      </section>
      <div className="ops-reset"><button className="quiet" onClick={reset}>重置整个案例</button><span>重置后可重新演示，刷新页面也会恢复初始状态。</span></div>
    </>}
    <footer className="ops-footer">此页为可公开展示的角色流程。正式工作人员入口应登录鉴权，并在每次资料检索、工具调用与措施发布时校验权限。现有<a href="/review">回答评审记录</a>仍需管理凭据。</footer>
  </main>;
}
