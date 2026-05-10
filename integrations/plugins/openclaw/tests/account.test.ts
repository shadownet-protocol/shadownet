import { describe, expect, it } from "vitest";

import { Value } from "@sinclair/typebox/value";

import {
  ShadownetChannelConfigSchema,
  listAccountIds,
  resolveAccount,
  SHADOWNET_DEFAULTS,
} from "../src/account";

describe("ShadownetChannelConfigSchema", () => {
  it("accepts a minimal valid config", () => {
    const ok = Value.Check(ShadownetChannelConfigSchema, {
      endpoint: "https://sidecar.example.test/u/alice@sh4dow.org/mcp",
      token: "0123456789abcdef",
      secret: "0123456789abcdef0123456789abcdef",
    });
    expect(ok).toBe(true);
  });

  it("rejects extra properties", () => {
    const ok = Value.Check(ShadownetChannelConfigSchema, {
      endpoint: "https://example.test",
      token: "0123456789abcdef",
      secret: "0123456789abcdef0123456789abcdef",
      somethingExtra: 42,
    });
    expect(ok).toBe(false);
  });

  it("rejects short token / short secret", () => {
    expect(
      Value.Check(ShadownetChannelConfigSchema, {
        endpoint: "https://example.test",
        token: "short",
        secret: "0123456789abcdef0123456789abcdef",
      }),
    ).toBe(false);
    expect(
      Value.Check(ShadownetChannelConfigSchema, {
        endpoint: "https://example.test",
        token: "0123456789abcdef",
        secret: "tooshort",
      }),
    ).toBe(false);
  });
});

describe("resolveAccount", () => {
  it("returns sane defaults for a fully-populated single-account config", () => {
    const cfg = {
      channels: {
        shadownet: {
          endpoint: "https://example.test/u/alice/mcp",
          token: "tk-abcdef0123456789",
          secret: "0123456789abcdef0123456789abcdef",
        },
      },
    };
    const account = resolveAccount(cfg);
    expect(account.accountId).toBe(SHADOWNET_DEFAULTS.accountId);
    expect(account.endpoint).toBe("https://example.test/u/alice/mcp");
    expect(account.webhookPath).toBe(SHADOWNET_DEFAULTS.webhookPath);
    expect(account.dmPolicy).toBe("allowlist");
    expect(account.allowedShadownames).toEqual([]);
    expect(account.rateLimitPerMinute).toBe(SHADOWNET_DEFAULTS.rateLimitPerMinute);
    expect(account.enabled).toBe(true);
  });

  it("respects explicit dmPolicy + allowedShadownames + rate-limit overrides", () => {
    const cfg = {
      channels: {
        shadownet: {
          endpoint: "https://example.test/u/alice/mcp",
          token: "tk-abcdef0123456789",
          secret: "0123456789abcdef0123456789abcdef",
          dmPolicy: "open" as const,
          allowedShadownames: ["bob@sh4dow.org", "*"],
          rateLimitPerMinute: 600,
          webhookPath: "/custom/inbox",
        },
      },
    };
    const account = resolveAccount(cfg);
    expect(account.dmPolicy).toBe("open");
    expect(account.allowedShadownames).toEqual(["bob@sh4dow.org", "*"]);
    expect(account.rateLimitPerMinute).toBe(600);
    expect(account.webhookPath).toBe("/custom/inbox");
  });

  it("falls back to empty defaults when section is missing", () => {
    const account = resolveAccount({});
    expect(account.endpoint).toBe("");
    expect(account.token).toBe("");
    expect(account.secret).toBe("");
    expect(account.dmPolicy).toBe("allowlist");
  });
});

describe("listAccountIds", () => {
  it("returns the default account id when none configured", () => {
    expect(listAccountIds({})).toEqual([]);
    expect(
      listAccountIds({ channels: { shadownet: { endpoint: "x", token: "y", secret: "z".repeat(32) } } }),
    ).toEqual([SHADOWNET_DEFAULTS.accountId]);
  });
});
