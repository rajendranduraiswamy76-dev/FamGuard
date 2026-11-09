import { describe, expect, it } from "vitest";

import { SSEParser, readChatEvents, toChatEvent } from "@/lib/sse";
import type { ChatEvent } from "@/lib/types";

function streamOf(chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      chunks.forEach((c) => controller.enqueue(encoder.encode(c)));
      controller.close();
    },
  });
}

async function collect(stream: ReadableStream<Uint8Array>): Promise<ChatEvent[]> {
  const events: ChatEvent[] = [];
  for await (const e of readChatEvents(stream)) events.push(e);
  return events;
}

describe("SSEParser", () => {
  it("parses frames split at arbitrary points", () => {
    const parser = new SSEParser();
    const text = 'event: token\ndata: {"text":"Hel"}\n\nevent: token\ndata: {"text":"lo"}\n\n';
    const events = [];
    for (const ch of text) events.push(...parser.push(ch));
    events.push(...parser.flush());
    expect(events).toEqual([
      { event: "token", data: '{"text":"Hel"}' },
      { event: "token", data: '{"text":"lo"}' },
    ]);
  });

  it("handles CRLF, comments, multi-line data and missing event names", () => {
    const parser = new SSEParser();
    const events = parser.push(": keep-alive\r\nevent: x\r\ndata: a\r\ndata: b\r\n\r\ndata: plain\r\n\r\n");
    expect(events).toEqual([
      { event: "x", data: "a\nb" },
      { event: "message", data: "plain" },
    ]);
  });

  it("does not split a CRLF that arrives across two chunks", () => {
    const parser = new SSEParser();
    expect(parser.push("data: 1\r")).toEqual([]);
    expect(parser.push("\n\r\n")).toEqual([{ event: "message", data: "1" }]);
  });

  it("flushes a final event without trailing blank line", () => {
    const parser = new SSEParser();
    parser.push('event: done\ndata: {"conversation_id":null}');
    expect(parser.flush()).toEqual([{ event: "done", data: '{"conversation_id":null}' }]);
  });
});

describe("toChatEvent / readChatEvents", () => {
  it("ignores unknown events and malformed JSON", () => {
    expect(toChatEvent({ event: "ping", data: "{}" })).toBeNull();
    expect(toChatEvent({ event: "token", data: "{oops" })).toBeNull();
  });

  it("yields typed events in order from fragmented network chunks", async () => {
    const events = await collect(
      streamOf([
        'event: filter\ndata: {"member_id":null,"member":"John","cate',
        'gory":"identitycards"}\n\nevent: tok',
        'en\ndata: {"text":"Hi"}\n\nevent: sources\ndata: []\n\n',
        'event: done\ndata: {"conversation_id":"c1"}\n\n',
      ]),
    );
    expect(events.map((e) => e.event)).toEqual(["filter", "token", "sources", "done"]);
    expect(events[0]).toEqual({
      event: "filter",
      data: { member_id: null, member: "John", category: "identitycards" },
    });
  });

  it("decodes multi-byte characters split across chunks", async () => {
    const bytes = new TextEncoder().encode('event: token\ndata: {"text":"café"}\n\n');
    const split = bytes.indexOf(0xc3) + 1;
    const stream = new ReadableStream<Uint8Array>({
      start(c) {
        c.enqueue(bytes.slice(0, split));
        c.enqueue(bytes.slice(split));
        c.close();
      },
    });
    expect(await collect(stream)).toEqual([{ event: "token", data: { text: "café" } }]);
  });
});
