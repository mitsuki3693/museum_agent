"use client";
import {useEffect, useState, useRef} from "react";
import ChatComposer from "@/components/ChatComposer";
import CollectionPicker from "@/components/CollectionPicker";
import ArtworkCandidate from "@/components/ArtworkCandidate";
import PhotoResultView from "@/components/PhotoResultView";
import type {PhotoAction, PhotoResult} from "@/lib/photo-types";
import {ListenButton} from "@/components/MuseumSpeech";
import {museumApi, askMuseum, identifyMuseumPhoto, QuestionRequest} from "@/lib/museum-api";
import {MuseumObject, workName} from "@/lib/museum-types";
import {createRequestId} from "@/lib/request-id";
import RoutePanel from "@/components/RoutePanel";
import type {FacilityResult, RouteOptions, RoutePreferences, VisitRoute} from "@/lib/route-types";

type Source = {id: string; title: string; content: string; source_url: string; fetched_at: string; license: string; attribution?: string; license_url?: string; source_kind?: string; narrator?: string};
type Candidate = {id: string; title: string; artist?: string};
type Result = {trace_id: string; status: string; answer: string; mode: string; context_notice?: string; candidates?: Candidate[]; claims: {text: string; source_id: string; quote: string}[]; sources: Source[]; route_options?: RouteOptions; route_preferences?: RoutePreferences; route?: VisitRoute; facilities?: FacilityResult};
type Turn = {id: string; question: string; image?: string; photoQuestion?: string; photoAction?: PhotoAction; pending: boolean; result?: Result; photo?: PhotoResult; error?: string; request?: QuestionRequest};
type Health = {model_configured: boolean; storage: string; corpus_count: number};
const labels: Record<string, string> = {needs_confirmation: "先确认作品", answered: "附引用依据", retrieval_only: "原始资料", insufficient_evidence: "资料不足", verification_failed: "讲解尚未通过核对", service_unavailable: "服务暂不可用", route_setup: "确认参观偏好", route_ready: "参观顺序建议", route_unavailable: "暂不能规划这条路线", facility_found: "馆方设施信息", facility_unavailable: "设施资料不足"};
const styles = [["brief", "简明版"], ["deep", "深入一点"], ["children", "讲给孩子听"]];

export default function Home() {
  const [health, setHealth] = useState<Health | null>(null), [items, setItems] = useState<MuseumObject[]>([]);
  const [selected, setSelected] = useState("");
  const [selectedAsSimilar, setSelectedAsSimilar] = useState<false | "similar" | "number">(false), [photoParent, setPhotoParent] = useState("");
  const [photoQuestion, setPhotoQuestion] = useState(""), [collectionRequest, setCollectionRequest] = useState(0);
  const [turns, setTurns] = useState<Turn[]>([]), [busy, setBusy] = useState(false), [error, setError] = useState("");
  const [feedback, setFeedback] = useState<Record<string, string>>({}), [generation, setGeneration] = useState(0);
  const token = useRef(""), inFlight = useRef(false), end = useRef<HTMLDivElement>(null);
  const photos = useRef(new Set<string>());
  useEffect(() => {
    Promise.all([museumApi("health"), museumApi("objects")]).then(([h, o]) => {setHealth(h); setItems(o);}).catch(e => setError(e.message));
    const urls = photos.current;
    return () => {urls.forEach(url => URL.revokeObjectURL(url)); urls.clear();};
  }, []);
  useEffect(() => {end.current?.scrollIntoView({behavior: "smooth", block: "end"});}, [turns]);

  async function ensureSession() {
    if (!token.current) token.current = (await museumApi("sessions", {method: "POST"})).token;
    return token.current;
  }
  function updateTurn(id: string, patch: Partial<Turn>) {
    setTurns(current => current.map(turn => turn.id === id ? {...turn, ...patch} : turn));
  }
  async function ask(text: string, objectOverride = selected, mode = "brief", action = "question", retry?: Turn, route?: RoutePreferences) {
    if (!text.trim() || inFlight.current) return;
    inFlight.current = true; setBusy(true); setError("");
    const id = retry?.id || createRequestId();
    const request = retry?.request || {query: text.trim(), object_id: objectOverride, mode, action, request_id: createRequestId(), ...(route ? {route} : {})};
    if (retry) updateTurn(id, {pending: true, error: undefined});
    else setTurns(current => [...current, {id, question: text, pending: true, request}]);
    try {
      const session = await ensureSession();
      const result = await askMuseum(session, request);
      updateTurn(id, {pending: false, result});
    } catch (e) {updateTurn(id, {pending: false, error: (e as Error).message});}
    finally {inFlight.current = false; setBusy(false);}
  }
  async function identify(file: File, text: string) {
    if (inFlight.current) return;
    inFlight.current = true; setBusy(true); setError(""); setSelected(""); setSelectedAsSimilar(false);
    const id = createRequestId(), image = URL.createObjectURL(file);
    photos.current.add(image);
    const question = text || (photoParent ? photoQuestion : "");
    setTurns(current => [...current, {id, question: question || "帮我看看这件作品", photoQuestion: question, image, pending: true}]);
    try {
      const session = await ensureSession();
      const photo = await identifyMuseumPhoto(session, file, photoParent || undefined);
      updateTurn(id, {pending: false, photo});
      setPhotoParent(""); setPhotoQuestion("");
    } catch (e) {updateTurn(id, {pending: false, error: (e as Error).message});}
    finally {inFlight.current = false; setBusy(false);}
  }
  async function clear() {
    if (inFlight.current) return;
    inFlight.current = true; setBusy(true); setError("");
    try {if (token.current) await museumApi("session/close", {method: "POST", headers: {Authorization: `Bearer ${token.current}`}});}
    catch { /* An expired session must not prevent starting a fresh conversation. */ }
    finally {
      token.current = ""; photos.current.forEach(url => URL.revokeObjectURL(url)); photos.current.clear();
      setTurns([]); setSelected(""); setSelectedAsSimilar(false); setPhotoParent(""); setPhotoQuestion(""); setFeedback({}); setGeneration(value => value + 1);
      window.speechSynthesis?.cancel(); inFlight.current = false; setBusy(false);
    }
  }
  async function rate(id: string, kind: string) {
    try {
      await museumApi("feedback", {method: "POST", headers: {"Content-Type": "application/json", Authorization: `Bearer ${token.current}`}, body: JSON.stringify({trace_id: id, kind})});
      setFeedback(current => ({...current, [id]: kind}));
    } catch (e) {setError((e as Error).message);}
  }
  function choose(id: string, question = "") {
    if (inFlight.current) return;
    setSelected(id); setSelectedAsSimilar(false); setPhotoParent(""); setPhotoQuestion("");
    const item = items.find(work => work.id === id);
    if (question) ask(question, id);
    else ask(`请简明讲解${item ? `《${workName(item)}》` : "这件作品"}。`, id, "brief", "narration");
  }
  function narrate(id: string, style: string) {
    setSelected(id);
    const text: Record<string, string> = {brief: "先给我一个简明版。", deep: "想再深入了解一下。", children: "请换成适合孩子听的讲法。"};
    ask(text[style], id, style, "narration");
  }
  function unselect() {setSelected(""); setSelectedAsSimilar(false); setPhotoParent(""); setPhotoQuestion(""); document.getElementById("question")?.focus();}
  function openPhotoAlternative(action: "search" | "browse") {
    unselect();
    if (action === "browse") setCollectionRequest(value => value + 1);
  }
  async function actOnPhoto(turn: Turn, action: PhotoAction, objectId?: string) {
    if (inFlight.current || !turn.photo) return;
    inFlight.current = true; setBusy(true); setError("");
    let saved = false;
    try {
      await museumApi("photo-actions", {method: "POST", headers: {"Content-Type": "application/json", Authorization: `Bearer ${token.current}`},
        body: JSON.stringify({trace_id: turn.photo.trace_id, action, object_id: objectId})});
      updateTurn(turn.id, {photoAction: action}); saved = true;
      if (action === "retry") {
        setSelected(""); setSelectedAsSimilar(false); setPhotoParent(turn.photo.trace_id); setPhotoQuestion(turn.photoQuestion || "");
        document.querySelector(".chat-footer")?.scrollIntoView({behavior: "smooth", block: "end"});
      } else if (action === "confirm" || action === "view_similar" || action === "view_number") {
        setSelected(objectId!); setSelectedAsSimilar(action === "view_number" ? "number" : action === "view_similar" ? "similar" : false); setPhotoParent(""); setPhotoQuestion("");
      } else {
        setSelected(""); setSelectedAsSimilar(false); setPhotoParent(""); setPhotoQuestion("");
      }
    } catch (e) {setError((e as Error).message);}
    finally {inFlight.current = false; setBusy(false);}
    if (saved && (action === "search" || action === "browse")) {
      // Focus after React has re-enabled the composer input.
      requestAnimationFrame(() => openPhotoAlternative(action));
    }
    if (saved && objectId && (action === "confirm" || action === "view_similar" || action === "view_number")) {
      const item = items.find(work => work.id === objectId), name = item ? workName(item) : "这件馆藏";
      if (action === "confirm" && turn.photoQuestion) await ask(turn.photoQuestion, objectId);
      else await ask(action === "view_number" ? `请简明介绍按编号找到的馆藏《${name}》。这不代表我确认了照片中的作品。` : action === "view_similar" ? `请简明介绍这件相似馆藏《${name}》。这不代表我确认了照片中的作品。` : `请简明讲解《${name}》。`, objectId, "brief", "narration");
    }
  }
  function candidateCards(rows: Candidate[], question = "") {
    return <div className="discovery-candidates">{rows.map(candidate => <ArtworkCandidate key={candidate.id}
      work={{...candidate, ...items.find(item => item.id === candidate.id)}} disabled={busy} onConfirm={id => choose(id, question)}/>)}
      <button className="quiet" disabled={busy} onClick={unselect}>都不是，我再补充一下</button></div>;
  }
  const selectedItem = items.find(item => item.id === selected);
  return <main className="chat-shell">
    <header className="chat-header">
      <a className="brand" href="/" aria-label="MUSE · 你的博物馆随行助手"><strong className="brand-wordmark">MUSE<span className="brand-dot" aria-hidden="true">.</span></strong><span className="brand-tagline">YOUR MUSEUM COMPANION</span></a>
      <div className="header-actions"><CollectionPicker items={items} busy={busy} onChoose={choose} openRequest={collectionRequest}/><button className="quiet" onClick={clear} disabled={busy}>新对话</button></div>
    </header>
    <section className="chat-log" aria-label="藏品对话">
      <div className="chat-width">
        <div className="welcome-message"><span className="assistant-avatar" aria-hidden="true">m</span><div>
          <div className="welcome-label">LOOK CLOSER. <span>看见更多。</span></div><h1>这一件，有什么故事？</h1>
          <p>拍一张照片，或直接提问。先听一小段，再聊你感兴趣的细节。</p>
          {!turns.length && <div className="starter-works">{items.filter(item => item.image_url).slice(0, 3).map(item => <button key={item.id} disabled={busy} onClick={() => choose(item.id)}>
            <img src={item.image_url} alt=""/><span>{workName(item)}<small>从这件开始 ↗</small></span>
          </button>)}</div>}
        </div></div>
        {health && !health.model_configured && <div className="notice">当前仅提供资料检索，AI 回答与照片识别尚未启用。</div>}
        <div className="turns" aria-live="polite">
          {turns.map(turn => <article className="turn" key={turn.id}>
            <div className="question">{turn.image && <img className="sent-photo" src={turn.image} alt="你发送的作品照片"/>}<p>{turn.question}</p></div>
            <div className="assistant-message"><span className="assistant-avatar" aria-hidden="true">m</span><div className="answer">
              <div className="answer-label">MUSE{turn.result && <span>{labels[turn.result.status] || turn.result.status}</span>}</div>
              {turn.pending && <p className="loading" role="status"><span className="loading-dot"/>{turn.image ? "正在对照馆藏图片，请稍候…" : turn.request?.action === "route" ? "正在查看路线与设施资料…" : "正在查找资料与讲解依据…"}</p>}
              {turn.error && <div className="error" role="alert"><p>{turn.error}</p>{turn.request ? <button className="quiet" disabled={busy} onClick={() => ask(turn.question, "", "brief", "question", turn)}>重试这条消息</button> : <><p>这次请求未完成，不能据此判断照片里是哪件作品。可以稍后重新发送，或换一种方式查找。</p><div className="photo-recovery-actions"><button className="quiet" disabled={busy} onClick={() => openPhotoAlternative("search")}>输入名称或展签文字</button><button className="quiet" disabled={busy} onClick={() => openPhotoAlternative("browse")}>浏览馆藏</button></div></>}</div>}
              {turn.photo && <><PhotoResultView result={turn.photo} items={items} busy={busy} action={turn.photoAction} onAction={(action, id) => actOnPhoto(turn, action, id)}/>
                <div className="feedback photo-feedback"><span>识别反馈</span>{[["helpful", "候选有帮助"], ["wrong_fact", "候选不对"], ["not_answered", "仍需帮助"]].map(([kind, label]) => <button key={kind} disabled={busy} aria-pressed={feedback[turn.photo!.trace_id] === kind} onClick={() => rate(turn.photo!.trace_id, kind)}>{label}</button>)}{feedback[turn.photo.trace_id] && <small>已记录</small>}</div></>}
              {turn.result && <>
                {turn.result.context_notice && <p className="photo-context-notice">{turn.result.context_notice}</p>}
                {turn.result.claims.length ? turn.result.claims.map((claim, index) => <p className="answer-text" key={index}>{claim.text}</p>) : <p className="answer-text">{turn.result.answer}</p>}
                {Boolean(turn.result.candidates?.length) && candidateCards(turn.result.candidates!)}
                {turn.result.route_options && <RoutePanel options={turn.result.route_options} initial={turn.result.route_preferences} route={turn.result.route} facilities={turn.result.facilities} busy={busy} onChoose={choose} onFind={query => ask(query, selected, "brief", "route")}
                  onPlan={preferences => {const options = turn.result!.route_options!; const start = options.starts?.find(item => item.id === preferences.start_id)?.title || preferences.start_id;
                    const interests = options.interests?.filter(item => preferences.interests.includes(item.id)).map(item => item.label).join("、") || "综合参观";
                    ask(`请安排 ${preferences.minutes} 分钟的路线，从${start}出发，偏好${interests}${preferences.step_free ? "，需要全程无台阶" : ""}${preferences.skip_ids.length ? "，跳过已看过的展厅" : ""}。`, selected, "brief", "route", undefined, preferences);}}/>}
                {turn.result.status === "answered" && <div className="answer-actions"><ListenButton text={turn.result.answer}/>
                  {turn.result.sources.length === 1 && styles.filter(([style]) => style !== turn.result!.mode).map(([style, label]) => <button className="style-button" key={style} disabled={busy} onClick={() => narrate(turn.result!.sources[0].id, style)}>{label} ↗</button>)}
                </div>}
                {turn.result.sources.some(source => source.source_kind === "official_transcript") && <p className="narration-note">依据馆方英文讲解改写，非馆方官方中文稿。</p>}
                {turn.result.sources.length > 0 && <details className="sources"><summary>查看讲解依据与来源 · {turn.result.sources.length} 份资料</summary>
                  {turn.result.claims.map((claim, index) => <blockquote key={index}>{claim.quote}</blockquote>)}
                  {turn.result.sources.map(source => <details key={source.id}><summary>{source.title}{source.source_kind === "official_transcript" ? " · 馆方英文讲解" : ""}</summary>
                    {source.narrator && <p>原讲解者：{source.narrator}</p>}<pre>{source.content}</pre>
                    <p className="source-meta">{source.attribution} · {source.license} · 采集于 {source.fetched_at.slice(0, 10)}</p>
                    <a href={source.source_url} target="_blank" rel="noreferrer">打开馆方原始页面 ↗</a>{source.license_url && <a href={source.license_url} target="_blank" rel="noreferrer">许可说明</a>}
                  </details>)}
                </details>}
                <div className="feedback"><span>有帮助吗？</span>{[["helpful", "有帮助"], ["wrong_fact", "事实有误"], ["not_answered", "没有解答"]].map(([kind, label]) => <button disabled={busy} aria-pressed={feedback[turn.result!.trace_id] === kind} key={kind} onClick={() => rate(turn.result!.trace_id, kind)}>{label}</button>)}{feedback[turn.result.trace_id] && <small>已记录</small>}</div>
              </>}
            </div></div>
          </article>)}
          <div ref={end}/>
        </div>
      </div>
    </section>
    <footer className="chat-footer"><div className="chat-width">
      {error && <p role="alert" className="error">{error}</p>}
      <ChatComposer key={generation} busy={busy} configured={Boolean(health?.model_configured)} selectedName={selectedItem ? workName(selectedItem) : ""}
        similar={selectedAsSimilar === "similar"} numberClue={selectedAsSimilar === "number"} retrying={Boolean(photoParent)}
        onSend={(text, file) => {if (file) identify(file, text); else {setPhotoParent(""); setPhotoQuestion(""); ask(text);}}} onUnselect={unselect} onRoute={text => {setPhotoParent(""); setPhotoQuestion(""); ask(text.trim() || "帮我规划参观路线。", selected, "brief", "route");}}/>
      <details className="demo-info"><summary>关于这个演示</summary><p>资料为固定快照，不提供实时展位、开放时间或票价。本演示与馆方无隶属关系。语音由浏览器识别，确认文字后才发送；请勿输入个人敏感信息。{health?.storage === "memory" && "演示会话保留 30 分钟，服务重启后聊天和反馈会清空。"}{health?.storage === "mongo" && "会话到期后需重新开始；执行记录与反馈会保留供项目复盘，不保存原始照片。"} <a href="/review">回答评审记录</a> · <a href="/staff-demo">馆方协作演示</a> · <a href="/conservation-demo">文保工作台演示</a></p></details>
    </div></footer>
  </main>;
}
