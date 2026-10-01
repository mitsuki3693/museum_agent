"use client";
import ArtworkCandidate from "./ArtworkCandidate";
import type {MuseumObject} from "@/lib/museum-types";
import type {PhotoAction, PhotoResult} from "@/lib/photo-types";

export default function PhotoResultView({result, items, busy, action, onAction}: {
  result: PhotoResult; items: MuseumObject[]; busy: boolean; action?: PhotoAction;
  onAction: (action: PhotoAction, id?: string) => void;
}) {
  const labels = {likely_match: "请确认作品", uncertain: "还需确认", no_reliable_match: "暂未找到可靠匹配"};
  return <section className="photo-result" aria-label="照片识别结果">
    {result.status !== "service_unavailable" && result.match_state && <strong className="photo-state">{labels[result.match_state]}</strong>}
    <p className="answer-text">{result.message}</p>
    {result.candidates.map(candidate => <ArtworkCandidate key={candidate.id}
      work={{...candidate, ...items.find(item => item.id === candidate.id)}} disabled={busy}
      onConfirm={id => onAction("confirm", id)}/>)}
    {Boolean(result.similar_candidates?.length) && <>
      <p className="photo-explanation">找到了一件外观相似的馆藏，可以先了解它。它不一定就是照片中的作品。</p>
      {result.similar_candidates!.map(candidate => <div key={candidate.id}>
        {!!candidate.shared_features?.length && <p className="photo-features">外观相似处：{candidate.shared_features.join("、")}。</p>}
        <ArtworkCandidate work={{...candidate, ...items.find(item => item.id === candidate.id)}} disabled={busy}
          similar actionLabel="查看这件相似作品" onConfirm={id => onAction("view_similar", id)}/>
      </div>)}
    </>}
    {!!result.next_steps?.length && <ul className="photo-next-steps">{result.next_steps.map(step => <li key={step}>{step}</li>)}</ul>}
    <button className="quiet photo-retry" disabled={busy} onClick={() => onAction("retry")}>不是这件／补拍一张</button>
    {action && <p className="photo-action-note" role="status">{action === "confirm" ? "已记录你对候选的确认。" : action === "view_similar" ? "正在查看相似馆藏，原照片的身份仍未确认。" : "请用下方的拍照或相册按钮补充照片。"}</p>}
  </section>;
}
