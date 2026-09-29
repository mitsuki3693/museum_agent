/** UUID for request deduplication, including mobile browsers on LAN HTTP. */
export function createRequestId(): string {
  const crypto = globalThis.crypto;
  if (typeof crypto?.randomUUID === "function") return crypto.randomUUID();
  // getRandomValues is also available outside secure contexts; randomUUID is not.
  if (typeof crypto?.getRandomValues !== "function") {
    throw new Error("当前浏览器无法创建请求，请使用新版 Safari 或 Chrome 重试。");
  }
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, value => value.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}
