"use client";
import {useEffect, useMemo, useRef, useState} from "react";
import {museumApi} from "@/lib/museum-api";

type Point = {x: number; y: number; label: string; instruction: string};
type FloorCase = {
  available: true; id: string; title: string; venue: string; level: string;
  attribution: string; source_url: string; view_box: [number, number, number, number];
  waypoints: Point[]; tiles: {id: string; url: string; x: number; y: number; size: number}[];
};

// One curated demonstration, independent of the itinerary planner.
// Coordinates use the original map's pixel space; images are not redrawn.
export default function RouteMapDemo() {
  const [map, setMap] = useState<FloorCase | null>(null), [error, setError] = useState("");
  const [loaded, setLoaded] = useState<Set<string>>(new Set());
  const [progress, setProgress] = useState(0), [playing, setPlaying] = useState(false);
  const [zoom, setZoom] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const controller = new AbortController();
    museumApi("routes/floor-demo", {signal: controller.signal}).then(result => {
      if (!controller.signal.aborted && result.available) setMap(result);
    }).catch(() => {if (!controller.signal.aborted) setError("地图暂时无法加载，请稍后重试。");});
    return () => controller.abort();
  }, []);
  const distances = useMemo(() => {
    const result = [0];
    map?.waypoints.slice(1).forEach((point, i) => {
      const previous = map.waypoints[i];
      result.push(result[i] + Math.hypot(point.x - previous.x, point.y - previous.y));
    });
    return result;
  }, [map]);
  const ready = !!map && loaded.size === map.tiles.length && !error;
  useEffect(() => {
    if (!playing || !ready) return;
    const timer = window.setInterval(() => setProgress(value => Math.min(100, value + .35)), 100);
    return () => window.clearInterval(timer);
  }, [playing, ready]);
  useEffect(() => {if (progress === 100 || error) setPlaying(false);}, [progress, error]);
  if (!map) return error ? <p className="map-demo-note">{error}</p> : null;

  const distance = distances[distances.length - 1] * progress / 100;
  const segment = Math.max(0, Math.min(map.waypoints.length - 2, distances.findIndex(value => value > distance) - 1));
  const index = progress === 100 ? map.waypoints.length - 2 : segment;
  const from = map.waypoints[index], to = map.waypoints[index + 1];
  const fraction = (distance - distances[index]) / (distances[index + 1] - distances[index]);
  const dot = {x: from.x + (to.x - from.x) * fraction, y: from.y + (to.y - from.y) * fraction};
  const end = map.waypoints[map.waypoints.length - 1];
  const arrived = progress === 100;
  const path = map.waypoints.map(p => `${p.x},${p.y}`).join(" ");
  const travelled = [...map.waypoints.slice(0, index + 1), dot].map(p => `${p.x},${p.y}`).join(" ");
  const [bx, by, bw, bh] = map.view_box;
  const width = zoom ? bw / 2.2 : bw, height = zoom ? bh / 2.2 : bh;
  const vx = zoom ? Math.max(bx, Math.min(bx + bw - width, dot.x - width / 2)) : bx;
  const vy = zoom ? Math.max(by, Math.min(by + bh - height, dot.y - height / 2)) : by;
  const labelX = Math.max(vx + 130, Math.min(vx + width - 130, dot.x));

  function canvas() {
    return <div className="real-map-frame">
      <svg className="real-floor-map" viewBox={`${vx} ${vy} ${width} ${height}`} role="img" aria-label={`${map!.level} 真实楼层平面图，模拟路线 ${map!.title}`}>
        {map!.tiles.map(tile => <image key={tile.id} href={tile.url} x={tile.x} y={tile.y} width={tile.size} height={tile.size}
          onLoad={() => setLoaded(current => current.has(tile.id) ? current : new Set([...current, tile.id]))}
          onError={() => setError("地图图片未加载完整，请刷新后再播放。")}/>)}
        {ready && <g className="floor-route-overlay">
          <polyline className="real-route-outline" points={path}/>
          <polyline className="real-route-future" points={path}/>
          <polyline className="real-route-done" points={travelled}/>
          <circle className="real-route-start" cx={map!.waypoints[0].x} cy={map!.waypoints[0].y} r="13"/>
          <circle className="real-route-end" cx={end.x} cy={end.y} r="21"/>
          <text className="real-map-destination" x={end.x + 46} y={end.y + 14}>终点 · {end.label}</text>
          <g className="real-map-here" transform={`translate(${dot.x} ${dot.y})`}>
            <circle className="real-position-halo" r="35"/><circle className="real-position" r="17"/>
          </g>
          <g transform={`translate(${labelX} ${dot.y - 100})`}>
            <rect className="real-here-label" x="-120" y="-10" width="240" height="64" rx="14"/>
            <text className="real-here-text" textAnchor="middle" y="35">{arrived ? "已到达" : "你在这里"}</text>
          </g>
        </g>}
      </svg>
      <span className="real-map-level">{map!.level}</span>
      {!ready && <div className="real-map-loading" role="status">{error || "正在加载馆方平面图…"}</div>}
    </div>;
  }
  function controls() {
    return <>
      <p className="map-current"><span className="map-location-dot"/>{arrived ? `已到达 ${end.label}` : `${from.label} · ${from.instruction}`}</p>
      <div className="map-demo-controls">
        <button type="button" disabled={!ready} onClick={() => {if (arrived) setProgress(0); setPlaying(value => !value);}}>{playing ? "暂停模拟" : arrived ? "重新播放" : "开始模拟行走"}</button>
        <button type="button" disabled={!ready} aria-pressed={zoom} onClick={() => setZoom(value => !value)}>{zoom ? "查看全程" : "放大跟随"}</button>
        <button type="button" disabled={!ready} onClick={() => {setProgress(0); setPlaying(false);}}>回到起点</button>
      </div>
      <label className="map-progress">行走进度 <input aria-label="模拟行走进度" type="range" min="0" max="100" step="1" value={progress} disabled={!ready}
        onChange={event => {setPlaying(false); setProgress(Number(event.target.value));}}/><span>{Math.round(progress)}%</span></label>
    </>;
  }
  return <section className="route-map-demo" aria-label="真实地图导航案例">
    <div className="map-demo-heading"><strong>在地图上跟着走</strong><span>模拟定位</span></div>
    <p className="real-map-title">{map.title}<small>{map.venue} · 固定演示路线</small></p>
    {canvas()}
    <button type="button" className="map-expand" onClick={() => dialog.current?.showModal()}>展开地图 ↗</button>
    {controls()}
    <p className="map-demo-note">馆方真实底图；位置与行走过程为模拟。此案例展示一段固定通道，与参观偏好设置独立。</p>
    <a className="map-attribution" href={map.source_url} target="_blank" rel="noreferrer">{map.attribution} · 查看官方地图 ↗</a>
    <dialog ref={dialog} className="real-map-dialog" aria-label="展开的真实地图导航案例">
      <div className="map-demo-heading"><strong>{map.title} · 模拟定位</strong><button type="button" autoFocus onClick={() => dialog.current?.close()}>关闭大图 ×</button></div>
      {canvas()}{controls()}
      <p className="map-demo-note">{map.attribution} · 位置与移动为模拟</p>
    </dialog>
  </section>;
}
