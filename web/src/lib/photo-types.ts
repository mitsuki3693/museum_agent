export type PhotoCandidate = {id: string; title: string; artist?: string; source_url?: string; shared_features?: string[]};
export type PhotoResult = {
  trace_id: string; status: string; match_state?: "likely_match" | "uncertain" | "no_reliable_match";
  message: string; candidates: PhotoCandidate[]; similar_candidates?: PhotoCandidate[]; number_candidates?: PhotoCandidate[]; next_steps?: string[];
  retake_count?: number;
};
export type PhotoAction = "confirm" | "view_similar" | "view_number" | "retry" | "reject" | "search" | "browse";
