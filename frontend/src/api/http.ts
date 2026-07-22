import axios, { AxiosError } from "axios";

// baseURL 默认空字符串（= 同源）。
// 调用方写 http.get('/api/xxx')，最终请求就是 /api/xxx。
// 避免历史 /api/api 重复路径 bug。
// 开发环境可在 .env.local 设 VITE_API_DOMAIN=http://localhost:8000。
const baseURL = import.meta.env.VITE_API_DOMAIN || "";

// 默认超时调到 8s。后端 mDNS + HTTP 总等待 <= 3s + 兜底；
// 这里只是防御性兜底，不是用来掩盖 mDNS 解析慢。
export const http = axios.create({
  baseURL,
  timeout: 8000,
  withCredentials: false,
});

export function toErrorMessage(err: unknown): string {
  if (axios.isAxiosError(err)) {
    const e = err as AxiosError<any>;
    const status = e.response?.status;
    const data = e.response?.data;

    if (typeof data === "string") return data;
    if (data && typeof data === "object") {
      if (typeof (data as any).detail === "string") return (data as any).detail;
      return JSON.stringify(data);
    }
    return `HTTP Error${status ? ` (${status})` : ""}: ${e.message}`;
  }
  if (err instanceof Error) return err.message;
  return String(err);
}