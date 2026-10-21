"use client";
import {useEffect,useState} from "react";
import {sitePath} from "@/lib/site-mode";
import "./pages-demo.css";

const stops = {toilet:{label:"厕所",x:460,y:330},exit:{label:"出口",x:100,y:330},stairs:{label:"楼梯",x:460,y:80},desk:{label:"服务台",x:100,y:80}};
type Target=keyof typeof stops;
export default function PagesRoute({staff=false}:{staff?:boolean}){
  const [target,setTarget]=useState<Target>("toilet"),[progress,setProgress]=useState(0),[playing,setPlaying]=useState(false);
  const [reviewed,setReviewed]=useState(false),[confirmed,setConfirmed]=useState(false);
  useEffect(()=>{if(!playing)return;const timer=window.setInterval(()=>setProgress(p=>Math.min(100,p+2)),180);return()=>window.clearInterval(timer);},[playing]);
  useEffect(()=>{if(progress===100)setPlaying(false);},[progress]);
  const dest=stops[target];
  const points=confirmed?[[280,360],[280,300],[220,300],[220,210],[dest.x,210],[dest.x,dest.y]]:[[280,360],[280,210],[dest.x,210],[dest.x,dest.y]];
  const lengths=points.slice(1).map((p,i)=>Math.hypot(p[0]-points[i][0],p[1]-points[i][1]));
  const total=lengths.reduce((a,b)=>a+b,0);let left=total*progress/100,position=points[0];
  for(let i=0;i<lengths.length;i++){if(left<=lengths[i]){const f=lengths[i]?left/lengths[i]:0;position=[points[i][0]+(points[i+1][0]-points[i][0])*f,points[i][1]+(points[i+1][1]-points[i][1])*f];break;}left-=lengths[i];}
  function reset(){setPlaying(false);setProgress(0);setReviewed(false);setConfirmed(false);}
  return <main className="ops-shell pages-route"><header className="ops-header"><a className="brand" href={sitePath("/")}><strong>MUSE.</strong></a><span>{staff?"馆方协作案例":"路线与设施演示"}</span><a href={sitePath(staff?"conservation-demo/":"staff-demo/")}>{staff?"文保工作台":"馆方协作"}</a><a href={sitePath("/")}>返回游客端</a></header>
    <section className="ops-intro"><h1>{staff?"复核一次提醒，调整一条路线。":"从这里，到你想去的地方。"}</h1><p>{staff?"模拟聚集与环境提醒，由工作人员复核后分流。":"选一个目的地，看看位置沿着路线移动。"}</p><div className="ops-demo-notice"><strong>虚构平面图 · 模拟定位</strong>布局、人数、环境读数及路径均为项目编写的展示案例，不是 V&A 实馆地图，也不能用于现场导航。</div></section>
    <div className="pages-map-layout"><section><div className="pages-destinations" aria-label="选择目的地">{Object.entries(stops).map(([id,stop])=><button key={id} className="quiet" aria-pressed={target===id} onClick={()=>{setTarget(id as Target);setProgress(0);setPlaying(false);}}>{stop.label}</button>)}</div>
      <svg className="pages-floor" viewBox="0 0 560 410" role="img" aria-label={`虚构一层平面图，从入口到${dest.label}的${confirmed?"调整后":"原始"}示意路线`}>
        <rect x="20" y="20" width="520" height="370" rx="12" fill="#231b2c" stroke="#ac98bc" strokeWidth="2"/>
        <path d="M180 20V175H20 M350 20V175H540 M20 245H180V390 M350 390V245H540" fill="none" stroke="#ac98bc" strokeWidth="3"/>
        <g fill="#d5c8df" fontSize="15"><text x="217" y="95">展厅 A</text><text x="218" y="135">绘画示例</text><text x="390" y="280">展厅 B</text><text x="60" y="280">休息区</text><text x="240" y="385">参观入口</text></g>
        {staff&&<><rect x="255" y="245" width="52" height="48" rx="8" fill={confirmed?"#71475a":"#a86d3e"} opacity=".7"/><text x="320" y="265" fill="#f1d0ac" fontSize="13">{confirmed?"模拟限制区":"模拟聚集区"}</text></>}
        <polyline points={points.map(p=>p.join(",")).join(" ")} fill="none" stroke="#b89bd4" strokeWidth="5" strokeDasharray="9 7"/>
        {Object.entries(stops).map(([id,stop])=><g key={id}><circle cx={stop.x} cy={stop.y} r="7" fill={id===target?"#f1e879":"#b7adbf"}/><text x={stop.x} y={stop.y-17} textAnchor="middle" fill="#f5f0f8" fontSize="15">{stop.label}</text></g>)}
        <circle cx={position[0]} cy={position[1]} r="8" fill="#f1e879" stroke="#141117" strokeWidth="3"/><text x={position[0]} y={position[1]+23} textAnchor="middle" fill="#f1e879" fontSize="13">你在这里（模拟）</text>
      </svg>
      <div className="pages-map-controls"><button className="primary" onClick={()=>{if(progress===100)setProgress(0);setPlaying(p=>!p);}}>{playing?"暂停演示":progress===100?"重新播放":"播放行走"}</button><button className="quiet" onClick={()=>{setPlaying(false);setProgress(0);}}>回到起点</button><span role="status">{progress===100?`已到达${dest.label}（模拟）`:`前往${dest.label} · ${progress}%`}</span></div><label className="pages-progress">模拟行走进度<input type="range" min="0" max="100" value={progress} onChange={e=>{setPlaying(false);setProgress(Number(e.target.value));}}/></label>
    </section><aside className="pages-map-notes">{staff?<><h2>先复核，再确认</h2><p>模拟聚集：24人<br/>模拟相对湿度：67% RH</p><p>本案例以65% RH作为演示提醒线。人流与湿度同时变化不证明因果关系，也不能据此判定藏品受损。</p><details onToggle={e=>{if(e.currentTarget.open)setReviewed(true);}}><summary>查看案例依据</summary><p>项目自编规则 DEMO-01：发现聚集与环境提醒后，工作人员复核现场和传感器情况，再决定是否分流。这里的阈值不是通用文保标准。</p></details><button className="primary" disabled={!reviewed||confirmed} onClick={()=>{setConfirmed(true);setProgress(0);setPlaying(false);}}>{confirmed?"已确认 · 仅本页模拟":"确认模拟分流"}</button><p role="status">{confirmed?"示意路线已绕开模拟限制区；没有发布真实通行限制。":"展开依据后可确认，随后左侧路线会变化。"}</p><button className="quiet" onClick={reset}>重置案例</button></>:<><h2>演示如何使用</h2><p>切换上方目的地，播放或拖动行走进度。路线只用于说明“当前位置—目的地—路径”的交互。</p><p>本页不会读取 GPS，也不会根据图片推断位置。实际参观应查看馆方标识和工作人员指引。</p><a href={sitePath("staff-demo/")}>体验工作人员分流案例</a></>}</aside></div>
    <footer className="ops-footer">本页状态仅存在于浏览器内存。刷新会重置，不向服务器发送操作。</footer>
  </main>;
}
