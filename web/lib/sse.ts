/**
 * A minimal server-sent-events frame parser for a fetch() ReadableStream.
 *
 * Not EventSource: the browser's EventSource cannot send the Authorization header our proxy
 * needs, and even through the proxy we want one hand-rolled reducer that treats SSE and the
 * polling fallback identically (see hooks/useJobLive.ts). Lines starting with ":" are SSE
 * comments (the server's keepalive pings) and are discarded, not surfaced as frames.
 */

export interface SseFrame {
  id?: string;
  event?: string;
  data: string;
}

function parseFrame(raw: string): SseFrame | null {
  const dataLines: string[] = [];
  let id: string | undefined;
  let event: string | undefined;
  for (const line of raw.split("\n")) {
    if (line === "" || line.startsWith(":")) continue;
    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    const value = colon === -1 ? "" : line.slice(colon + 1).replace(/^ /, "");
    if (field === "id") id = value;
    else if (field === "event") event = value;
    else if (field === "data") dataLines.push(value);
  }
  if (dataLines.length === 0 && event === undefined && id === undefined) return null;
  return { id, event, data: dataLines.join("\n") };
}

export async function* readSseFrames(
  body: ReadableStream<Uint8Array>,
  /** Called on every chunk received, including ones that only carried a keepalive comment.
   * Lets a caller distinguish "no frames yet" from "the connection has gone silent." */
  onActivity?: () => void,
): AsyncGenerator<SseFrame> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      onActivity?.();
      buffer += decoder.decode(value, { stream: true });
      let boundary = buffer.indexOf("\n\n");
      while (boundary !== -1) {
        const raw = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        const frame = parseFrame(raw);
        if (frame) yield frame;
        boundary = buffer.indexOf("\n\n");
      }
    }
    const trailing = parseFrame(buffer);
    if (trailing) yield trailing;
  } finally {
    reader.releaseLock();
  }
}
