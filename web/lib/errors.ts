/**
 * One error shape for the whole app. The Django API itself returns three slightly different
 * bodies depending on code path: the app's own {error, message, errors} envelope (ApiError,
 * views.py), Django Ninja's built-in auth failure {detail: "Unauthorized"}, and the SSE view's
 * separately hand-written 401 {error, message}. This normalizer tolerates all three.
 */

export interface ApiErrorInfo {
  status: number;
  code: string;
  message: string;
  errors: string[];
}

interface RawErrorBody {
  error?: string;
  detail?: string;
  message?: string;
  errors?: unknown;
}

export async function readApiError(res: Response): Promise<ApiErrorInfo> {
  const body: RawErrorBody = await res.json().catch(() => ({}) as RawErrorBody);
  return {
    status: res.status,
    code: body.error ?? body.detail ?? `http_${res.status}`,
    message: body.message ?? body.detail ?? res.statusText ?? "Something went wrong.",
    errors: Array.isArray(body.errors) ? body.errors.map(String) : [],
  };
}

export class ApiClientError extends Error {
  status: number;
  code: string;
  errors: string[];

  constructor(info: ApiErrorInfo) {
    super(info.message);
    this.name = "ApiClientError";
    this.status = info.status;
    this.code = info.code;
    this.errors = info.errors;
  }
}

export function isQuotaExceeded(err: unknown): boolean {
  return err instanceof ApiClientError && err.status === 429 && err.code === "quota_exceeded";
}

export function isRateLimited(err: unknown): boolean {
  return err instanceof ApiClientError && err.status === 429 && err.code === "rate_limited";
}
