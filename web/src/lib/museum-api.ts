import {createRequestId} from "./request-id";
import type {RoutePreferences} from "./route-types";

export async function museumApi(path: string, options: RequestInit = {}) {
  const response = await fetch(`/api/museum/${path}`, options);
  if (!response.headers.get("content-type")?.includes("application/json")) {
    throw new Error("暂时连接不上服务，请稍后重试。");
  }
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "请求未完成，请稍后重试。");
  return data;
}

export type QuestionRequest = {query: string; object_id: string; mode: string; action: string; request_id?: string; route?: RoutePreferences};

export function askMuseum(token: string, request: QuestionRequest) {
  return museumApi("chat", {
    method: "POST",
    headers: {"Content-Type": "application/json", Authorization: `Bearer ${token}`},
    body: JSON.stringify({...request, query: request.query.trim(), request_id: request.request_id || createRequestId()}),
  });
}

export function identifyMuseumPhoto(token: string, file: File, parentTraceId?: string) {
  const body = new FormData();
  body.append("photo", file);
  if (parentTraceId) body.append("parent_trace_id", parentTraceId);
  return museumApi("recognize", {method: "POST", headers: {Authorization: `Bearer ${token}`}, body});
}
