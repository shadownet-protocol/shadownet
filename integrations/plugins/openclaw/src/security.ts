// DM allowlist + rate-limit helpers for the Shadownet channel plugin.

import type { ResolvedShadownetAccount, ShadownetDmPolicy } from "./types";

export function isShadownameAllowed(
  shadowname: string,
  account: ResolvedShadownetAccount,
): boolean {
  if (account.dmPolicy === "open") {
    if (account.allowedShadownames.includes("*")) return true;
    return account.allowedShadownames.length === 0 || account.allowedShadownames.includes(shadowname);
  }
  // allowlist: explicit shadowname or "*" wildcard
  return (
    account.allowedShadownames.includes(shadowname) ||
    account.allowedShadownames.includes("*")
  );
}

export function describeDmPolicyDecision(
  shadowname: string,
  account: ResolvedShadownetAccount,
): { allowed: boolean; reason: string } {
  if (isShadownameAllowed(shadowname, account)) {
    return { allowed: true, reason: "allowed by " + account.dmPolicy };
  }
  if (account.dmPolicy === "allowlist" && account.allowedShadownames.length === 0) {
    return {
      allowed: false,
      reason: "dmPolicy=allowlist but allowedShadownames is empty — all senders are rejected.",
    };
  }
  return { allowed: false, reason: `${shadowname} is not in allowedShadownames for this account.` };
}

// Per-account fixed-window rate limiter. In-process only.
export class ShadownetRateLimiter {
  private counts = new Map<string, { windowStart: number; count: number }>();
  private readonly windowMs: number;

  constructor(
    private readonly limitPerMinute: number,
    windowMs = 60_000,
  ) {
    this.windowMs = windowMs;
  }

  check(key: string, now = Date.now()): boolean {
    const entry = this.counts.get(key);
    if (!entry || now - entry.windowStart >= this.windowMs) {
      this.counts.set(key, { windowStart: now, count: 1 });
      return true;
    }
    entry.count += 1;
    return entry.count <= this.limitPerMinute;
  }

  reset(): void {
    this.counts.clear();
  }
}

export type { ShadownetDmPolicy };
