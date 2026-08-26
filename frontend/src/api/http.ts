import axios, { AxiosError } from "axios";

const baseURL = import.meta.env.VITE_API_DOMAIN || "";

export function createHttpClient(base = "", timeout = 8000) {
  return axios.create({
    baseURL: base,
    timeout,
    withCredentials: false,
  });
}

export const http = createHttpClient(baseURL, 8000);
export const httpLong = createHttpClient(baseURL, 60000);

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
