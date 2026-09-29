import {createRequestId} from "./request-id";

export async function museumApi(path: string, options: RequestInit = {}) {
  const response = await fetch(`/api/museum/${path}`, options);
  if (!response.headers.get("content-type")?.includes("application/json")) {
    throw new Error("暂时连接不上服务，请稍后重试。");
  }
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "请求未完成，请稍后重试。");
  return data;
}

export type QuestionRequest = {query: string; object_id: string; mode: string; action: string; request_id?: string};

export function askMuseum(token: string, request: QuestionRequest) {
  return museumApi("chat", {
    method: "POST",
    headers: {"Content-Type": "application/json", Authorization: `Bearer ${token}`},
    body: JSON.stringify({...request, query: request.query.trim(), request_id: request.request_id || createRequestId()}),
  });
}

export function identifyMuseumPhoto(token: string, file: File) {
  const body = new FormData();
  body.append("photo", file);
  return museumApi("recognize", {method: "POST", headers: {Authorization: `Bearer ${token}`}, body});
}
