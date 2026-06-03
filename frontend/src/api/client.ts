export class ApiError extends Error {
  status: number;
  body: string;

  constructor(message: string, status: number, body: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
  }
}

export interface ApiClientOptions {
  baseUrl?: string;
  timeoutMs?: number;
  retries?: number;
}

const defaultOptions: Required<ApiClientOptions> = {
  baseUrl: "",
  timeoutMs: 30000,
  retries: 1
};

export const AUTH_TOKEN_STORAGE_KEY = "ai_visual_agent_auth_token";

export function getStoredAuthToken(): string {
  return window.localStorage.getItem(AUTH_TOKEN_STORAGE_KEY) ?? "";
}

export function setStoredAuthToken(token: string): void {
  if (token) window.localStorage.setItem(AUTH_TOKEN_STORAGE_KEY, token);
  else window.localStorage.removeItem(AUTH_TOKEN_STORAGE_KEY);
}

export async function apiRequest<T>(
  path: string,
  init: RequestInit = {},
  options: ApiClientOptions = {}
): Promise<T> {
  const config = { ...defaultOptions, ...options };
  const url = `${config.baseUrl}${path}`;
  const method = (init.method || "GET").toUpperCase();
  let lastError: unknown = null;

  for (let attempt = 0; attempt <= config.retries; attempt += 1) {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), config.timeoutMs);
    try {
      const response = await fetch(url, {
        ...init,
        headers: buildHeaders(init),
        signal: controller.signal
      });
      window.clearTimeout(timeout);
      if (!response.ok) {
        const body = await response.text();
        throw new ApiError(body || `${response.status} ${response.statusText}`, response.status, body);
      }
      if (response.status === 204) return null as T;
      return (await response.json()) as T;
    } catch (error) {
      window.clearTimeout(timeout);
      lastError = error;
      if (!shouldRetry(error, method) || attempt === config.retries) break;
      await wait(250 * (attempt + 1));
    }
  }

  if (lastError instanceof Error) throw lastError;
  throw new Error("API request failed.");
}

function buildHeaders(init: RequestInit): HeadersInit {
  const token = getStoredAuthToken();
  const headers = new Headers(init.headers ?? {});
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (init.body instanceof FormData) {
    return headers;
  }
  if (!headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  return headers;
}

function shouldRetry(error: unknown, method: string): boolean {
  if (!["GET", "HEAD", "OPTIONS"].includes(method)) return false;
  if (error instanceof ApiError) return error.status >= 500 || error.status === 429;
  return true;
}

function wait(ms: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}
