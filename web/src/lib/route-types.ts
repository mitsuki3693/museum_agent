export type RoutePreferences = {minutes: number; start_id: string; interests: string[]; step_free: boolean; skip_ids: string[]};
export type FacilityResult = {
  items: {id: string; kind: string; title: string; location: string; note: string; map_url: string}[];
  sources: {id: string; title: string; url: string}[];
};
export type RouteOptions = {
  available: boolean; reason?: string; venue?: string; scope?: string; notice?: string;
  map_url?: string; checked_at?: string; synthetic?: boolean; default_start?: string;
  starts?: {id: string; title: string; level: string}[];
  interests?: {id: string; label: string}[];
};
export type VisitRoute = {
  venue: string; scope: string; start: {id: string; title: string; level: string};
  preferences: RoutePreferences; total_minutes: number; view_minutes: number; walk_minutes: number;
  remaining_minutes: number; time_note: string; notice: string; synthetic: boolean; checked_at: string;
  map_url: string; sources: {id: string; title: string; url: string}[];
  steps: {id: string; title: string; level: string; description: string; object_id?: string | null;
    view_minutes: number; walk_minutes: number; elapsed_minutes: number; reason: string;
    via: {id: string; title: string; level: string; instruction: string; step_free: boolean | null}[];
  }[];
};
