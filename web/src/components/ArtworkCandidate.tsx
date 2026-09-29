"use client";
import {useState} from "react";

const names:Record<string,string>={"artic-28560":"卧室","artic-16568":"睡莲","artic-27992":"大碗岛的星期天下午"};
export type CandidateWork={id:string;title:string;image_url?:string;source_url?:string;artist?:string;display_title?:string};
export default function ArtworkCandidate({work,onConfirm,disabled}:{work:CandidateWork;onConfirm:(id:string)=>void;disabled:boolean}){
 const [failed,setFailed]=useState(false);
 return <div className="artwork-candidate">
  {work.image_url&&!failed?<img src={work.image_url} alt={work.display_title||names[work.id]||work.title} onError={()=>setFailed(true)}/>:<p className="image-unavailable">这件作品暂缺可显示的图片，请核对名称或查看馆方页面。</p>}
  <div><strong>{work.display_title||names[work.id]||work.title}</strong><small>{work.title}{work.artist?` · ${work.artist}`:""}</small>
   <button className="primary" disabled={disabled} onClick={()=>onConfirm(work.id)}>就是这件，继续聊</button>
   {work.source_url&&<a href={work.source_url} target="_blank" rel="noreferrer">查看馆方原始页面 ↗</a>}
  </div>
 </div>;
}
