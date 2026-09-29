"use client";
import {ChangeEvent, useEffect, useRef, useState} from "react";

export type MuseumObject={id:string;title:string;source_url:string;image_url?:string};
const names:Record<string,string>={"artic-28560":"卧室","artic-16568":"睡莲","artic-27992":"大碗岛的星期天下午"};
type Candidate={id:string;title:string;artist:string};

export default function MuseumPhoto({configured,objects,ensureSession,onChoose,disabled}:{
 configured:boolean;objects:MuseumObject[];ensureSession:()=>Promise<string>;onChoose:(id:string)=>void;disabled:boolean;
}){
 const camera=useRef<HTMLInputElement>(null),album=useRef<HTMLInputElement>(null);
 const [file,setFile]=useState<File|null>(null),[preview,setPreview]=useState("");
 const [busy,setBusy]=useState(false),[message,setMessage]=useState("");
 const [candidates,setCandidates]=useState<Candidate[]>([]);
 useEffect(()=>{if(!file){setPreview("");return;}const url=URL.createObjectURL(file);setPreview(url);return()=>URL.revokeObjectURL(url);},[file]);
 function select(e:ChangeEvent<HTMLInputElement>){const next=e.target.files?.[0];e.target.value="";if(!next)return;
  if(next.size>8*1024*1024){setMessage("照片超过 8 MB，请压缩后重试。");return;}
  if(!["image/jpeg","image/png","image/webp"].includes(next.type)){setMessage("请选择 JPG、PNG 或 WebP 照片。");return;}
  setFile(next);setCandidates([]);setMessage("");
 }
 async function identify(){if(!file||busy||disabled)return;setBusy(true);setCandidates([]);setMessage("");
  try{const token=await ensureSession();const body=new FormData();body.append("photo",file);
   const response=await fetch("/api/museum/recognize",{method:"POST",headers:{Authorization:`Bearer ${token}`},body});
   if(!response.headers.get("content-type")?.includes("application/json"))throw new Error("服务暂不可用，请稍后重试。");
   const result=await response.json();if(!response.ok)throw new Error(result.detail||"识别未完成");
   setCandidates(result.candidates||[]);setMessage(result.message);
  }catch(e){setMessage((e as Error).message);}finally{setBusy(false);}
 }
 return <section className="photo-entry" aria-label="拍照听讲解">
  <div className="photo-intro"><div className="eyebrow">YOUR CURIOSITY, YOUR GUIDE</div><h1>你眼前的作品，<br/>有什么故事？</h1><p>拍下作品或展签。<br/>确认是哪一件，再听一段讲解。</p><div className="journey"><span>01 拍下来</span><span>02 确认作品</span><span>03 听故事</span></div></div>
  <div className="camera-card">{preview?<div className="photo-preview"><img src={preview} alt="你选择的照片，仅在确认识别后上传"/><button className="quiet" disabled={busy||disabled} onClick={()=>{setFile(null);setCandidates([]);setMessage("");}}>移除照片</button></div>:<div className="camera-illustration" aria-hidden="true"><svg viewBox="0 0 180 130" fill="none"><path d="M18 36V16h28M134 16h28v20M162 94v20h-28M46 114H18V94" stroke="currentColor" strokeWidth="2"/><rect x="43" y="41" width="94" height="64" rx="10" stroke="currentColor" strokeWidth="2"/><path d="M68 41l8-13h28l8 13" stroke="currentColor" strokeWidth="2"/><circle cx="90" cy="72" r="20" stroke="currentColor" strokeWidth="2"/><circle cx="90" cy="72" r="11" stroke="currentColor"/><circle cx="121" cy="53" r="3" fill="currentColor"/></svg><p>不必记住作品名，也不必输入编号</p></div>}
   <input ref={camera} type="file" accept="image/jpeg,image/png,image/webp" capture="environment" onChange={select} className="sr-only" aria-label="拍摄作品或展签"/>
   <input ref={album} type="file" accept="image/jpeg,image/png,image/webp" onChange={select} className="sr-only" aria-label="从相册选择照片"/>
   <div className="camera-actions"><button className="primary" disabled={busy||disabled} onClick={()=>camera.current?.click()}>拍一张照片</button><button className="secondary" disabled={busy||disabled} onClick={()=>album.current?.click()}>从相册选</button></div>
   {file&&<button className="primary identify" disabled={busy||disabled||!configured} onClick={identify}>{busy?"正在寻找对应作品…":"识别这张照片"}</button>}
   <p className="photo-note">{file?"点击识别后，照片会发送给 DeepSeek 处理；本项目不保存原图。":"尽量把作品拍完整；展签文字可以帮助识别。"}</p>
   {!configured&&<p className="photo-status">演示尚未连接 AI，照片可预览；识别与讲解待启用。</p>}
  </div>
  {(message||candidates.length>0)&&<div className="candidate-panel" aria-live="polite"><p>{message}</p>{candidates.map(c=><button className="candidate" key={c.id} disabled={disabled||busy} onClick={()=>onChoose(c.id)}><strong>{c.title}</strong><small>{c.artist}</small><span>是这件，开始讲解 →</span></button>)}{candidates.length>0&&<button className="quiet" onClick={()=>{setCandidates([]);setMessage("可以补拍展签，或从下面的名称搜索中寻找作品。");}}>都不是，重新找</button>}</div>}
  <div className="sample-section"><div className="sample-heading"><h2>不在馆里？先试一件作品</h2><span>示例来自芝加哥艺术博物馆</span></div><div className="sample-grid">{objects.filter(o=>o.image_url).map(o=><button className="sample-card" key={o.id} disabled={disabled||busy} onClick={()=>onChoose(o.id)}><div className="sample-image"><span>馆藏示例</span><img src={o.image_url} alt={o.title} loading="lazy" onError={e=>{e.currentTarget.style.display="none";}}/></div><div><strong>{names[o.id]||o.title}</strong><small>{o.title}</small><span>了解这件作品 ↗</span></div></button>)}</div><p className="photo-note">示例图片：Art Institute of Chicago，公有领域。当前仅覆盖 12 件示范藏品，不能识别所有博物馆展品。</p></div>
 </section>;
}
