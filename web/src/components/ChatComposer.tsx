"use client";
import {ChangeEvent, useEffect, useRef, useState} from "react";
import {VoiceInput} from "./MuseumSpeech";

export default function ChatComposer({busy, configured, selectedName, onSend, onUnselect}: {
  busy: boolean; configured: boolean; selectedName: string;
  onSend: (text: string, file: File | null) => void; onUnselect: () => void;
}) {
  const [text, setText] = useState(""), [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState(""), [error, setError] = useState("");
  const camera = useRef<HTMLInputElement>(null), album = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (!file) {setPreview(""); return;}
    const url = URL.createObjectURL(file);
    setPreview(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);
  function select(event: ChangeEvent<HTMLInputElement>) {
    const next = event.target.files?.[0];
    event.target.value = "";
    if (!next) return;
    if (!/image\/(jpeg|png|webp)/.test(next.type) || next.size > 8 * 1024 * 1024) {
      setError("请选择 8 MB 以内的 JPG、PNG 或 WebP 照片。"); return;
    }
    setFile(next); setError("");
  }
  function send() {
    if (busy || (!text.trim() && !file) || (file && !configured)) return;
    onSend(text.trim(), file); setText(""); setFile(null); setError("");
  }
  return <div className="composer-area">
    {selectedName && !file && <div className="context-chip"><span>正在聊 · {selectedName}</span><button type="button" disabled={busy} onClick={onUnselect}>换一件 ×</button></div>}
    <form className="composer" onSubmit={event => {event.preventDefault(); send();}}>
      {file && <div className="attachment-preview">
        {preview && <img src={preview} alt="待发送的照片"/>}
        <div><strong>照片已选好</strong><small>也可以加上你想问的问题</small></div>
        <button type="button" className="icon-button" aria-label="移除照片" disabled={busy} onClick={() => setFile(null)}>×</button>
      </div>}
      <label className="sr-only" htmlFor="question">你的问题</label>
      <textarea id="question" value={text} onChange={event => setText(event.target.value)}
        placeholder={file ? "这件作品让你好奇的是什么？" : selectedName ? "继续问，或拍下一件作品…" : "拍张照片，或说说你眼前的好奇…"}
        rows={2} maxLength={600} disabled={busy}
        onKeyDown={event => {if (event.key === "Enter" && (event.ctrlKey || event.metaKey) && !event.nativeEvent.isComposing) {event.preventDefault(); send();}}}/>
      <div className="composer-actions">
        <input ref={camera} hidden type="file" accept="image/jpeg,image/png,image/webp" capture="environment" onChange={select}/>
        <input ref={album} hidden type="file" accept="image/jpeg,image/png,image/webp" onChange={select}/>
        <div className="input-tools">
          <button type="button" className="tool-button" disabled={busy || !configured} onClick={() => camera.current?.click()}>
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 5l1-2h6l1 2h4v15H4V5z"/><circle cx="12" cy="12" r="4"/></svg>拍照</button>
          <button type="button" className="tool-button" disabled={busy || !configured} onClick={() => album.current?.click()}>
            <svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="3" width="18" height="18" rx="3"/><path d="M3 17l6-6 5 5 3-3 4 4"/><circle cx="16" cy="8" r="1"/></svg>相册</button>
          <VoiceInput disabled={busy} onText={setText}/>
        </div>
        <button type="submit" className="send-button" disabled={busy || (!text.trim() && !file) || Boolean(file && !configured)}>{busy ? "处理中…" : "发送 ↑"}</button>
      </div>
    </form>
    {error && <p className="error" role="alert">{error}</p>}
    <p className="composer-note">{file ? "发送后，照片会交由 DeepSeek 处理；本项目不保存原图。" : "仅支持当前示范馆藏；回答附资料来源，不能确认时会说明。"}</p>
  </div>;
}
