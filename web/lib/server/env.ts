import "server-only";

export function apiBase(): string {
  return process.env.ATELIER_API_BASE ?? "http://localhost:8000/api/v1";
}

export function apiKey(): string {
  const key = process.env.ATELIER_API_KEY;
  if (!key) {
    throw new Error(
      "ATELIER_API_KEY is not set. Copy web/.env.local.example to web/.env.local and fill in " +
        "a key (uv run python manage.py create_org <slug> \"<name>\" prints one once).",
    );
  }
  return key;
}
