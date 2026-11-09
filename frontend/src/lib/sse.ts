import type { ChatEvent } from "./types";

export interface RawSSEEvent {
  event: string;
  data: string;
}

/** Incremental text/event-stream parser that tolerates frames split across network chunks. */
export class SSEParser {
  private buffer = "";
  private eventName = "";
  private dataLines: string[] = [];

  push(chunk: string): RawSSEEvent[] {
    this.buffer += chunk;
    const events: RawSSEEvent[] = [];
    let newline: number;
    while ((newline = this.buffer.search(/\r\n|\r|\n/)) !== -1) {
      const line = this.buffer.slice(0, newline);
      const sepLength = this.buffer.startsWith("\r\n", newline) ? 2 : 1;
      // A trailing lone \r might be the first half of \r\n; wait for more input.
      if (sepLength === 1 && this.buffer[newline] === "\r" && newline === this.buffer.length - 1) {
        break;
      }
      this.buffer = this.buffer.slice(newline + sepLength);
      const event = this.handleLine(line);
      if (event) events.push(event);
    }
    return events;
  }

  flush(): RawSSEEvent[] {
    const events: RawSSEEvent[] = [];
    if (this.buffer) {
      const event = this.handleLine(this.buffer);
      this.buffer = "";
      if (event) events.push(event);
    }
    const last = this.dispatch();
    if (last) events.push(last);
    return events;
  }

  private handleLine(line: string): RawSSEEvent | null {
    if (line === "") return this.dispatch();
    if (line.startsWith(":")) return null;
    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "event") this.eventName = value;
    else if (field === "data") this.dataLines.push(value);
    return null;
  }

  private dispatch(): RawSSEEvent | null {
    if (this.dataLines.length === 0) {
      this.eventName = "";
      return null;
    }
    const event = { event: this.eventName || "message", data: this.dataLines.join("\n") };
    this.eventName = "";
    this.dataLines = [];
    return event;
  }
}

const KNOWN_EVENTS = new Set(["filter", "token", "sources", "error", "done"]);

export function toChatEvent(raw: RawSSEEvent): ChatEvent | null {
  if (!KNOWN_EVENTS.has(raw.event)) return null;
  try {
    return { event: raw.event, data: JSON.parse(raw.data) } as ChatEvent;
  } catch {
    return null;
  }
}

export async function* readChatEvents(
  body: ReadableStream<Uint8Array>,
  signal?: AbortSignal,
): AsyncGenerator<ChatEvent> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  const parser = new SSEParser();
  try {
    while (true) {
      if (signal?.aborted) return;
      const { value, done } = await reader.read();
      if (done) break;
      for (const raw of parser.push(decoder.decode(value, { stream: true }))) {
        const event = toChatEvent(raw);
        if (event) yield event;
      }
    }
    for (const raw of parser.push(decoder.decode()).concat(parser.flush())) {
      const event = toChatEvent(raw);
      if (event) yield event;
    }
  } finally {
    reader.releaseLock();
  }
}
