"use client";
import {useEffect, useMemo, useState} from "react";
import type {VisitRoute} from "@/lib/route-types";

type Point = {id: string; title: string; level: string};
type Position = Point & {x: number; y: number};

export default function RouteMapDemo({route}: {route: VisitRoute}) {
  const points = useMemo(() => {
    const path: Point[] = [route.start];
    for (const stop of route.steps) for (const via of stop.via) {
      if (path[path.length - 1].id !== via.id) path.push({id: via.id, title: via.title, level: via.level});
    }
    return path;
  }, [route]);
  const levels = [...new Set(points.map(point => point.level))];
  const positions = new Map<string, Position>();
  for (const level of levels) {
    const floorPoints = points.filter((point, index) => point.level === level && points.findIndex(p => p.id === point.id) === index);
    floorPoints.forEach((point, index) => {
      const row = Math.floor(index / 3), column = row % 2 ? 2 - index % 3 : index % 3;
      positions.set(point.id, {...point, x: 58 + column * 112, y: 64 + row * 98});
    });
  }
  const [progress, setProgress] = useState(0), [playing, setPlaying] = useState(false);
  const [manualLevel, setManualLevel] = useState<string | null>(null);
  const last = points.length - 1;
  useEffect(() => {
    if (!playing) return;
    const timer = window.setInterval(() => setProgress(value => Math.min(last, value + .05)), 200);
    return () => window.clearInterval(timer);
  }, [playing, last]);
  useEffect(() => {if (progress >= last) setPlaying(false);}, [progress, last]);
  const index = Math.min(Math.floor(progress), last), fraction = progress - index;
  const from = positions.get(points[index].id)!, to = positions.get(points[Math.min(index + 1, last)].id)!;
  const crossing = from.level !== to.level;
  const location = crossing && fraction >= .5 ? to : from;
  const level = manualLevel || location.level;
  const floorPoints = [...positions.values()].filter(point => point.level === level);
  const height = Math.max(220, 120 + Math.floor((floorPoints.length - 1) / 3) * 98);
  const dot = crossing ? {x: location.x, y: location.y} : {x: from.x + (to.x - from.x) * fraction, y: from.y + (to.y - from.y) * fraction};
  const transitions = points.slice(1).flatMap((point, i) => points[i].level !== point.level ? [{from: points[i], to: point}] : []);
  const here = progress >= last ? `已到达 ${location.title}` : crossing ? `${from.title} → ${to.title} · 切换楼层` : `${from.title} → ${to.title}`;
  function play() {
    if (progress >= last) setProgress(0);
    setManualLevel(null); setPlaying(true);
  }
  return <section className="route-map-demo" aria-label="模拟定位路线图">
    <div className="map-demo-heading"><strong>跟着路线走</strong><span>DEMO · 模拟定位</span></div>
    <p className="map-demo-note">展厅连接取自本次路线；图形布局、点位和移动速度均为模拟，不是实馆平面图或实时导航。</p>
    <div className="map-levels" aria-label="查看楼层">{levels.map(item => <button type="button" key={item} aria-pressed={level === item} onClick={() => {setManualLevel(item); setPlaying(false);}}>{item}</button>)}</div>
    <svg className="floor-demo" viewBox={`0 0 340 ${height}`} role="img" aria-label={`${level} 路线示意，模拟位置${location.title}`}>
      <rect className="floor-boundary" x="16" y="22" width="308" height={height - 44} rx="16"/>
      <text className="floor-watermark" x="324" y={height - 7} textAnchor="end">SCHEMATIC / 示意图</text>
      {points.slice(1).map((point, i) => {
        const a = positions.get(points[i].id)!, b = positions.get(point.id)!;
        if (a.level !== level || b.level !== level) return null;
        return <line key={`${i}-path`} className={`map-path${progress >= i + 1 ? " travelled" : ""}`} x1={a.x} y1={a.y} x2={b.x} y2={b.y}/>;
      })}
      {floorPoints.map(point => <g key={point.id}>
        <rect className="map-room" x={point.x - 31} y={point.y - 28} width="62" height="57" rx="10"/>
        <circle className="map-stop" cx={point.x} cy={point.y} r="5"/>
        <text className="map-room-label" x={point.x} y={point.y + 44} textAnchor="middle">{point.title.split(" · ")[0]}</text>
      </g>)}
      {location.level === level && <g transform={`translate(${dot.x} ${dot.y})`}>
        <circle className="map-position-halo" r="15"/><circle className="map-position" r="8"/>
      </g>}
    </svg>
    {transitions.filter(change => change.from.level === level || change.to.level === level).map((change, i) => <p className="map-floor-change" key={i}>↕ {change.from.level} · {change.from.title.split(" · ")[0]} → {change.to.level} · {change.to.title.split(" · ")[0]}</p>)}
    <p className="map-current"><span className="map-location-dot"/>模拟位置：{here}</p>
    {manualLevel && manualLevel !== location.level && <p className="map-demo-note">当前位置在 {location.level}；点击播放将跟随位置切换楼层。</p>}
    <div className="map-demo-controls">
      <button type="button" onClick={() => playing ? setPlaying(false) : play()} disabled={last === 0}>{playing ? "暂停模拟" : progress >= last ? "重新播放" : "播放模拟定位"}</button>
      <button type="button" onClick={() => {setPlaying(false); setManualLevel(null); setProgress(value => Math.min(last, Math.floor(value) + 1));}} disabled={progress >= last}>到下一点</button>
      <button type="button" onClick={() => {setPlaying(false); setProgress(0); setManualLevel(null);}}>重置</button>
    </div>
  </section>;
}
