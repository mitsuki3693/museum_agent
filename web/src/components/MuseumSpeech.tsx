"use client";
import {useEffect,useRef,useState} from "react";
type Recognition={lang:string;interimResults:boolean;continuous:boolean;start:()=>void;stop:()=>void;abort:()=>void;onresult:((event:{resultIndex?:number;results:ArrayLike<ArrayLike<{transcript:string}> & {isFinal?:boolean}>})=>void)|null;onerror:((event:{error:string})=>void)|null;onend:(()=>void)|null};
type SpeechWindow=Window & {SpeechRecognition?:new()=>Recognition;webkitSpeechRecognition?:new()=>Recognition};

export function VoiceInput({onText,disabled}:{onText:(text:string)=>void;disabled:boolean}){
 const [supported,setSupported]=useState(false),[listening,setListening]=useState(false),[notice,setNotice]=useState("");const current=useRef<Recognition|null>(null);
 const timer=useRef<ReturnType<typeof setTimeout>|null>(null);
 function release(recognition:Recognition,updateState=true,abort=true){
  if(current.current!==recognition)return;
  current.current=null;
  if(timer.current!==null){clearTimeout(timer.current);timer.current=null;}
  recognition.onresult=null;recognition.onerror=null;recognition.onend=null;
  if(abort){try{recognition.abort();}catch{/* Browser may already have ended. */}}
  if(updateState)setListening(false);
 }
 useEffect(()=>{const w=window as SpeechWindow;setSupported(Boolean(w.SpeechRecognition||w.webkitSpeechRecognition));return()=>{if(current.current)release(current.current,false);};},[]);
 useEffect(()=>{if(disabled&&current.current)release(current.current);},[disabled]);
 function armTimeout(recognition:Recognition,ms:number){
  if(timer.current!==null)clearTimeout(timer.current);
  timer.current=setTimeout(()=>{
   if(current.current!==recognition)return;
   release(recognition);setNotice("录音已结束，未收到识别结果。可以再次点击语音，或直接打字。");
  },ms);
 }
 function toggle(){
  if(disabled)return;
  if(current.current){
   const active=current.current;
   try{active.stop();if(current.current===active)armTimeout(active,2000);}
   catch{release(active);setNotice("录音已结束，可以再次点击语音。");}
   return;
  }
  const w=window as SpeechWindow;const Constructor=w.SpeechRecognition||w.webkitSpeechRecognition;if(!Constructor)return;
  const recognition=new Constructor();current.current=recognition;recognition.lang="zh-CN";recognition.interimResults=false;recognition.continuous=false;
  recognition.onresult=e=>{
   if(current.current!==recognition)return;
   const result=e.results[e.resultIndex??0];if(result?.isFinal===false)return;
   const text=result?.[0]?.transcript?.trim().slice(0,600)??"";
   // Some browsers omit onend after the final result. Release here as well.
   release(recognition);
   if(text){onText(text);setNotice("已转成文字，请确认后发送。");}
   else setNotice("没有听清，可以再次点击语音，或直接打字。");
  };
  recognition.onerror=e=>{
   if(current.current!==recognition)return;
   release(recognition);setNotice(e.error==="not-allowed"||e.error==="service-not-allowed"
    ?"请允许浏览器使用麦克风，再点击语音；也可以直接打字。"
    :"此次语音未识别成功，可以再次点击语音，或直接打字。");
  };
  recognition.onend=()=>{if(current.current===recognition)release(recognition,true,false);};
  try{setListening(true);setNotice("");recognition.start();if(current.current===recognition)armTimeout(recognition,30000);}
  catch{release(recognition);setNotice("语音服务暂不可用，可以再次点击语音，或先打字。");}
 }
 return <div className="voice-input"><button type="button" className="tool-button" aria-label={listening?"停止录音":"语音输入"} title={supported?"语音由浏览器的识别服务处理；确认文字后才发送。":"当前浏览器不支持语音输入，可直接打字。"} disabled={disabled||!supported} onClick={toggle}><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="9" y="2" width="6" height="13" rx="3"/><path d="M5 10v2a7 7 0 0014 0v-2M12 19v3M8 22h8"/></svg>{listening?"停止":"语音"}</button>{notice&&<small className="voice-notice" role="status">{notice}</small>}</div>;
}

export function ListenButton({text}:{text:string}){
 const [supported,setSupported]=useState(false),[playing,setPlaying]=useState(false);
 useEffect(()=>{setSupported("speechSynthesis" in window);return()=>window.speechSynthesis?.cancel();},[]);
 function play(){if(playing){window.speechSynthesis.cancel();setPlaying(false);return;}window.speechSynthesis.cancel();const utterance=new SpeechSynthesisUtterance(text);utterance.lang="zh-CN";utterance.rate=.95;utterance.onend=()=>setPlaying(false);utterance.onerror=()=>setPlaying(false);window.speechSynthesis.speak(utterance);setPlaying(true);}
 return <button className="listen-button" disabled={!supported} onClick={play}>{playing?"■ 停止讲解":"▶ 听这段讲解"}</button>;
}
