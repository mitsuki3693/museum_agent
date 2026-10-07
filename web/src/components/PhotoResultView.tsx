"use client";
import ArtworkCandidate from "./ArtworkCandidate";
import type {MuseumObject} from "@/lib/museum-types";
import type {PhotoAction, PhotoResult} from "@/lib/photo-types";

export default function PhotoResultView({result, items, busy, action, onAction}: {
  result: PhotoResult; items: MuseumObject[]; busy: boolean; action?: PhotoAction;
  onAction: (action: PhotoAction, id?: string) => void;
}) {
  const unavailable = result.status === "service_unavailable";
  const exhausted = (result.retake_count || 0) >= 1;
  const closed = Boolean(action && action !== "reject");
  const candidates = unavailable ? [] : result.candidates.slice(0, 2);
  const similar = unavailable || candidates.length ? [] : (result.similar_candidates || []).slice(0, 2);
  const labels = {likely_match: "请确认作品", uncertain: "还需确认", no_reliable_match: "暂未找到可靠匹配"};
  const notes: Record<PhotoAction, string> = {
    confirm: "已记录你的确认，接下来讲解你选择的作品。",
    view_similar: "正在查看相似馆藏，原照片的身份仍未确认。",
    retry: unavailable ? "可以用下方按钮重新发送照片，不必因为服务故障换角度。" : "请补拍同一件作品的全貌或展签；下方可取消补拍。",
    reject: "已记录这些候选都不是。可以换一种方式查找。",
    search: "请在下方输入作品名称或展签文字，当前未选择任何作品。",
    browse: "已打开馆藏列表，可以按名称查找或直接选择作品。",
  };
  return <section className="photo-result" aria-label="照片识别结果">
    <strong className="photo-state">{unavailable ? "识别服务暂不可用" : result.match_state ? labels[result.match_state] : "请核对作品"}</strong>
    <p className="answer-text">{unavailable ? "这次识别没有完成，不代表照片有问题，也不代表图库中没有这件作品。" : result.message}</p>
    {!!candidates.length && <p className="photo-explanation">请对照图片和名称选择；如果都不符合，请选“都不是”。</p>}
    {candidates.map(candidate => <div key={candidate.id}>
      {!!candidate.shared_features?.length && <p className="photo-features">可对照的外观：{candidate.shared_features.join("、")}。这些相似处不能单独确认身份。</p>}
      <ArtworkCandidate work={{...candidate, ...items.find(item => item.id === candidate.id)}} disabled={busy || Boolean(action)}
        onConfirm={id => onAction("confirm", id)}/>
    </div>)}
    {!!similar.length && <>
      <p className="photo-explanation">以下仅为相似馆藏。选择后会讲解这件馆藏，不代表认出了你的照片。</p>
      {similar.map(candidate => <div key={candidate.id}>
        {!!candidate.shared_features?.length && <p className="photo-features">外观相似处：{candidate.shared_features.join("、")}。</p>}
        <ArtworkCandidate work={{...candidate, ...items.find(item => item.id === candidate.id)}} disabled={busy || Boolean(action)}
          similar actionLabel="了解这件相似馆藏" onConfirm={id => onAction("view_similar", id)}/>
      </div>)}
    </>}
    {!closed && <>
      {!unavailable && !exhausted && !!result.next_steps?.length && <ul className="photo-next-steps">{result.next_steps.slice(0, 2).map(step => <li key={step}>{step}</li>)}</ul>}
      {!unavailable && exhausted && <p className="photo-explanation">已经补拍过一次。如果仍不能确认，不用反复拍摄：可以输入作品名称、展签文字，或浏览馆藏。</p>}
      <div className="photo-recovery-actions">
        {!!(candidates.length || similar.length) && !action && <button className="quiet" disabled={busy} onClick={() => onAction("reject")}>都不是</button>}
        {(!exhausted || unavailable) && <button className="quiet" disabled={busy} onClick={() => onAction("retry")}>{unavailable ? "稍后重新发送照片" : "补拍全貌或展签"}</button>}
        <button className="quiet" disabled={busy} onClick={() => onAction("search")}>输入名称或展签文字</button>
        <button className="quiet" disabled={busy} onClick={() => onAction("browse")}>浏览馆藏</button>
      </div>
    </>}
    {action && <p className="photo-action-note" role="status">{notes[action]}</p>}
  </section>;
}
