// shadownet://connect URL parser (RFC-0007 amendment B).
//
// TypeScript port of `python-sdk/src/shadownet/connect/url.py`. The two
// implementations are conformance-tested against shared fixtures so
// plugins on either side parse identically.
//
// Two forms supported:
//   shadownet://connect?base=<https-url>&token=<jwt>       (inline)
//   shadownet://connect?base=<https-url>&handoff=<code>    (handoff)
// Exactly one of token/handoff is present.

export const CONNECT_SCHEME = "shadownet";
export const CONNECT_HOST = "connect";

export class ConnectUrlInvalidError extends Error {
  override readonly name = "ConnectUrlInvalidError";
}

export interface ConnectUrl {
  readonly baseUrl: string;
  readonly token: string | null;
  readonly handoff: string | null;
}

export function isInline(connectUrl: ConnectUrl): boolean {
  return connectUrl.token !== null;
}

export function isHandoff(connectUrl: ConnectUrl): boolean {
  return connectUrl.handoff !== null;
}

export function parseConnectUrl(url: string): ConnectUrl {
  let parsed: URL;
  try {
    parsed = new URL(url);
  } catch {
    throw new ConnectUrlInvalidError(`could not parse URL: ${url}`);
  }
  // `URL` strips the trailing ':' from the scheme.
  if (parsed.protocol !== `${CONNECT_SCHEME}:`) {
    throw new ConnectUrlInvalidError(
      `scheme must be '${CONNECT_SCHEME}', got '${parsed.protocol.replace(/:$/, "")}'`,
    );
  }
  if (parsed.host !== CONNECT_HOST) {
    throw new ConnectUrlInvalidError(`host must be '${CONNECT_HOST}', got '${parsed.host}'`);
  }
  // Allow empty path or '/'; anything else is reserved for future amendments.
  if (parsed.pathname !== "" && parsed.pathname !== "/") {
    throw new ConnectUrlInvalidError(`unexpected path component: '${parsed.pathname}'`);
  }

  const params = parsed.searchParams;
  const baseValues = params.getAll("base");
  if (baseValues.length !== 1) {
    throw new ConnectUrlInvalidError("exactly one 'base' parameter required");
  }
  const base = baseValues[0]!;
  let baseParsed: URL;
  try {
    baseParsed = new URL(base);
  } catch {
    throw new ConnectUrlInvalidError(`base URL is not a valid URL: ${base}`);
  }
  if (baseParsed.protocol !== "http:" && baseParsed.protocol !== "https:") {
    throw new ConnectUrlInvalidError(
      `base must use http(s) scheme, got '${baseParsed.protocol.replace(/:$/, "")}'`,
    );
  }
  if (!baseParsed.host) {
    throw new ConnectUrlInvalidError("base URL missing host");
  }

  const tokenValues = params.getAll("token");
  const handoffValues = params.getAll("handoff");
  if (tokenValues.length > 1 || handoffValues.length > 1) {
    throw new ConnectUrlInvalidError(
      "'token' and 'handoff' allow at most one value each",
    );
  }
  if ((tokenValues.length > 0) === (handoffValues.length > 0)) {
    throw new ConnectUrlInvalidError("exactly one of 'token' or 'handoff' must be set");
  }

  return {
    baseUrl: stripTrailingSlash(base),
    token: tokenValues.length > 0 ? tokenValues[0]! : null,
    handoff: handoffValues.length > 0 ? handoffValues[0]! : null,
  };
}

export interface FormatConnectUrlInput {
  readonly baseUrl: string;
  readonly token?: string | null;
  readonly handoff?: string | null;
}

export function formatConnectUrl(input: FormatConnectUrlInput): string {
  const hasToken = input.token != null && input.token !== "";
  const hasHandoff = input.handoff != null && input.handoff !== "";
  if (hasToken === hasHandoff) {
    throw new ConnectUrlInvalidError("exactly one of 'token' or 'handoff' must be set");
  }
  let baseParsed: URL;
  try {
    baseParsed = new URL(input.baseUrl);
  } catch {
    throw new ConnectUrlInvalidError(`invalid base URL: '${input.baseUrl}'`);
  }
  if (baseParsed.protocol !== "http:" && baseParsed.protocol !== "https:") {
    throw new ConnectUrlInvalidError(`invalid base URL: '${input.baseUrl}'`);
  }
  if (!baseParsed.host) {
    throw new ConnectUrlInvalidError(`invalid base URL: '${input.baseUrl}'`);
  }

  const params = new URLSearchParams();
  params.set("base", stripTrailingSlash(input.baseUrl));
  if (hasToken) {
    params.set("token", input.token!);
  } else {
    params.set("handoff", input.handoff!);
  }
  return `${CONNECT_SCHEME}://${CONNECT_HOST}?${params.toString()}`;
}

function stripTrailingSlash(s: string): string {
  return s.replace(/\/+$/, "");
}
