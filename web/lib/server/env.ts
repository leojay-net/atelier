import "server-only";

export function apiBase(): string {
  return process.env.ATELIER_API_BASE ?? "http://localhost:8000/api/v1";
}
