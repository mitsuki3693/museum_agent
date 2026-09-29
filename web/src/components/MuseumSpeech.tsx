"use client";
import {useEffect,useRef,useState} from "react";
type Recognition={lang:string;interimResults:boolean;continuous:boolean;start:()=>void;stop:()=>void;abort:()=>void;onresult:((event:{results:ArrayLike<ArrayLike<{transcript:string}>>})=>void)|null;onerror:((event:{error:string})=>void)|null;onend:(()=>void)|null};
type SpeechWindow=Window & {SpeechRecognition?:new()=>Recognition;webkitSpeechRecognition?:new()=>Recognition};

export function VoiceInput({onText,disabled}:{onText:(text:string)=>void;disabled:boolean}){
 const [supported,setSupported]=useState(false),[listening,setListening]=useState(false),[notice,setNotice]=useState("");const current=useRef<Recognition|null>(null);
 useEffect(()=>{const w=window as SpeechWindow;setSupported(Boolean(w.SpeechRecognition||w.webkitSpeechRecognition));return()=>current.current?.abort();},[]);
 function toggle(){if(listening){current.current?.stop();return;}const w=window as SpeechWindow;const Constructor=w.SpeechRecognition||w.webkitSpeechRecognition;if(!Constructor)return;
  const recognition=new Constructor();current.current=recognition;recognition.lang="zh-CN";recognition.interimResults=false;recognition.continuous=false;
  recognition.onresult=e=>{onText(e.results[0][0].transcript.slice(0,600));setNotice("已转成文字，请确认后发送。");};
  recognition.onerror=()=>{setNotice("无法识别语音，请检查麦克风权限，或改用打字。");setListening(false);};recognition.onend=()=>setListening(false);
  try{recognition.start();setListening(true);setNotice("");}catch{setNotice("语音服务暂不可用，可以先打字。");}
 }
 return <div className="voice-input"><button type="button" className="quiet" disabled={disabled||!supported} onClick={toggle}>{listening?"停止录音":"说出你的问题"}</button><small>{notice||(supported?"语音由浏览器的识别服务处理；确认文字后才发送。":"当前浏览器不支持语音输入，可直接打字。")}</small></div>;
}

export function ListenButton({text}:{text:string}){
 const [supported,setSupported]=useState(false),[playing,setPlaying]=useState(false);
 useEffect(()=>{setSupported("speechSynthesis" in window);return()=>window.speechSynthesis?.cancel();},[]);
 function play(){if(playing){window.speechSynthesis.cancel();setPlaying(false);return;}window.speechSynthesis.cancel();const utterance=new SpeechSynthesisUtterance(text);utterance.lang="zh-CN";utterance.rate=.95;utterance.onend=()=>setPlaying(false);utterance.onerror=()=>setPlaying(false);window.speechSynthesis.speak(utterance);setPlaying(true);}
 return <button className="listen-button" disabled={!supported} onClick={play}>{playing?"■ 停止讲解":"▶ 听这段讲解"}</button>;
}
