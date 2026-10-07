"use client";
import {useEffect, useRef, useState} from "react";
import {MuseumObject, workName} from "@/lib/museum-types";

export default function CollectionPicker({items, busy, onChoose, openRequest = 0}: {items: MuseumObject[]; busy: boolean; onChoose: (id: string) => void; openRequest?: number}) {
  const menu = useRef<HTMLDetailsElement>(null);
  const [filter, setFilter] = useState("");
  const search = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (openRequest && menu.current) {
      menu.current.open = true;
      setFilter("");
      search.current?.focus();
    }
  }, [openRequest]);
  const matches = items.filter(item => `${workName(item)} ${item.title}`.toLowerCase().includes(filter.trim().toLowerCase()));
  return <details className="collection-menu" ref={menu}>
    <summary>选件作品 <span aria-hidden="true">⌄</span></summary>
    <div className="collection-popover">
      <div className="collection-heading"><strong>示范馆藏</strong><button type="button" className="quiet" onClick={() => {if (menu.current) menu.current.open = false;}}>收起</button></div>
      <label className="sr-only" htmlFor="collection-search">查找作品</label>
      <input ref={search} id="collection-search" value={filter} onChange={event => setFilter(event.target.value)} placeholder="中文或英文作品名"/>
      <div className="collection-list">{matches.map(item => <button key={item.id} disabled={busy} onClick={() => {onChoose(item.id); if (menu.current) menu.current.open = false;}}>
        {item.image_url ? <img src={item.image_url} alt=""/> : <span className="thumbnail-empty">藏</span>}
        <span>{workName(item)}<small>{item.collection || "公开馆藏资料"}</small></span>
      </button>)}</div>
      {!matches.length && <p>没有找到这个名字，可以在对话里描述作品。</p>}
      <p className="footnote">这是固定资料的演示，不能确认实时展位与开放状态。</p>
    </div>
  </details>;
}
