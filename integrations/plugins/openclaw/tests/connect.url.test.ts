import { describe, expect, it } from "vitest";

import {
  CONNECT_HOST,
  CONNECT_SCHEME,
  ConnectUrlInvalidError,
  formatConnectUrl,
  isHandoff,
  isInline,
  parseConnectUrl,
} from "../src/connect/url";

describe("parseConnectUrl", () => {
  it("round-trips an inline (token) URL", () => {
    const url = formatConnectUrl({ baseUrl: "https://app.example", token: "tok-abc" });
    const parsed = parseConnectUrl(url);
    expect(parsed).toEqual({
      baseUrl: "https://app.example",
      token: "tok-abc",
      handoff: null,
    });
    expect(isInline(parsed)).toBe(true);
    expect(isHandoff(parsed)).toBe(false);
  });

  it("round-trips a handoff URL", () => {
    const url = formatConnectUrl({ baseUrl: "https://app.example", handoff: "ABC123-XYZ987" });
    const parsed = parseConnectUrl(url);
    expect(parsed).toEqual({
      baseUrl: "https://app.example",
      token: null,
      handoff: "ABC123-XYZ987",
    });
    expect(isHandoff(parsed)).toBe(true);
    expect(isInline(parsed)).toBe(false);
  });

  it("strips a trailing slash from base", () => {
    const parsed = parseConnectUrl(
      `${CONNECT_SCHEME}://${CONNECT_HOST}?base=https%3A%2F%2Fapp.example%2F&token=t`,
    );
    expect(parsed.baseUrl).toBe("https://app.example");
  });

  it("accepts http://localhost for self-hosts", () => {
    const url = formatConnectUrl({ baseUrl: "http://localhost:8080", token: "t" });
    const parsed = parseConnectUrl(url);
    expect(parsed.baseUrl).toBe("http://localhost:8080");
  });

  it("preserves JWT-shaped tokens through the parse/format round-trip", () => {
    const token = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJhbGljZSJ9.SflKxw_AdQssw5c";
    const url = formatConnectUrl({ baseUrl: "https://app.example", token });
    expect(parseConnectUrl(url).token).toBe(token);
  });

  describe("parse rejects", () => {
    it.each([
      ["wrong scheme", "https://connect?base=https://x&token=t", "scheme must be"],
      [
        "wrong host",
        "shadownet://other?base=https://x&token=t",
        "host must be",
      ],
      [
        "extra path",
        "shadownet://connect/extra?base=https://x&token=t",
        "unexpected path",
      ],
      [
        "missing base",
        "shadownet://connect?token=t",
        "exactly one 'base'",
      ],
      [
        "duplicate base",
        "shadownet://connect?base=https://x&base=https://y&token=t",
        "exactly one 'base'",
      ],
      [
        "neither token nor handoff",
        "shadownet://connect?base=https://x",
        "exactly one of 'token' or 'handoff'",
      ],
      [
        "both token and handoff",
        "shadownet://connect?base=https://x&token=t&handoff=h",
        "exactly one of",
      ],
      [
        "multiple token values",
        "shadownet://connect?base=https://x&token=a&token=b",
        "at most one",
      ],
      [
        "non-http base scheme",
        "shadownet://connect?base=ftp://x&token=t",
        "http\\(s\\) scheme",
      ],
      [
        "base with empty host",
        "shadownet://connect?base=https%3A%2F%2F&token=t",
        "base URL is not a valid URL|missing host",
      ],
    ])("rejects %s", (_, input, pattern) => {
      expect(() => parseConnectUrl(input)).toThrow(
        new RegExp(pattern),
      );
    });

    it("uses ConnectUrlInvalidError for rejections", () => {
      expect(() =>
        parseConnectUrl("not a url at all"),
      ).toThrow(ConnectUrlInvalidError);
    });
  });
});

describe("formatConnectUrl", () => {
  it("rejects when both token and handoff are set", () => {
    expect(() =>
      formatConnectUrl({
        baseUrl: "https://x",
        token: "t",
        handoff: "h",
      }),
    ).toThrow(ConnectUrlInvalidError);
  });

  it("rejects when neither is set", () => {
    expect(() => formatConnectUrl({ baseUrl: "https://x" })).toThrow(
      ConnectUrlInvalidError,
    );
  });

  it("rejects a malformed base URL", () => {
    expect(() => formatConnectUrl({ baseUrl: "not-a-url", token: "t" })).toThrow(
      /invalid base URL/,
    );
  });

  it("rejects an ftp base URL", () => {
    expect(() => formatConnectUrl({ baseUrl: "ftp://x", token: "t" })).toThrow(
      /invalid base URL/,
    );
  });
});
