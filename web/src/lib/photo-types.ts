export type PhotoCandidate = {id: string; title: string; artist?: string; source_url?: string; shared_features?: string[]};
export type PhotoResult = {
  trace_id: string; status: string; match_state?: "likely_match" | "uncertain" | "no_reliable_match";
  message: string; candidates: PhotoCandidate[]; similar_candidates?: PhotoCandidate[]; next_steps?: string[];
};
export type PhotoAction = "confirm" | "view_similar" | "retry";
