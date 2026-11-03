/**
 * JSON calls to the cockpit's REST API (docs/specs/spec_cockpit.md §15).
 *
 * - Paths are relative to `/api` on the page's own origin (the backend serves the frontend).
 * - The session cookie goes along; requests that change something carry Django's CSRF token.
 * - Errors are RFC 9457 problem documents: they become an `ApiError` with the parsed problem.
 * - A network failure, or a 502 / 504 from a proxy, means the backend is not running:
 *   `BackendDown`, which the shell turns into the "backend unreachable" page.
 * - A 401 sends the browser to the login page (and back afterwards) unless the caller opts out.
 */

export interface Problem {
  type?: string;
  title?: string;
  status?: number;
  detail?: string;
  hint?: string;
  docs?: string;
  retry_after_s?: number;
  [key: string]: unknown;
}

export class ApiError extends Error {
  readonly status: number;
  readonly problem: Problem;

  constructor(status: number, problem: Problem) {
    super(problem.detail ?? problem.title ?? `HTTP ${status}`);
    this.name = "ApiError";
    this.status = status;
    this.problem = problem;
  }
}

export class BackendDown extends Error {
  constructor() {
    super("the cockpit backend is not reachable");
    this.name = "BackendDown";
  }
}

export interface HttpInit extends Omit<RequestInit, "body"> {
  /** sent as JSON */
  body?: unknown;
  /** false: a 401 is thrown to the caller; the login and first-user pages need that */
  authRedirect?: boolean;
}

/** Methods that change nothing: Django wants no CSRF token for them. */
const READ_ONLY = ["GET", "HEAD", "OPTIONS", "TRACE"];
/** Pages that work without a session: a 401 there must not redirect to the login again. */
const NO_LOGIN_NEEDED = ["/login", "/setup-account"];

/** Django's CSRF cookie (set by any GET of the API). */
export function csrfToken(): string | undefined {
  if (typeof document === "undefined") return undefined;
  for (const part of document.cookie.split(";")) {
    const [name, ...value] = part.trim().split("=");
    if (name === "csrftoken") return decodeURIComponent(value.join("="));
  }
  return undefined;
}

function toLogin(): void {
  if (typeof window === "undefined") return;
  const { pathname, search } = window.location;
  if (NO_LOGIN_NEEDED.some((page) => pathname === page || pathname.startsWith(`${page}/`))) return;
  window.location.assign(`/login/?next=${encodeURIComponent(pathname + search)}`);
}

async function problemOf(res: Response): Promise<Problem> {
  const type = res.headers.get("Content-Type") ?? "";
  if (type.includes("json")) {
    try {
      return { status: res.status, ...(await res.json()) };
    } catch {
      // a broken body: fall through to the status line
    }
  }
  return { status: res.status, title: res.statusText || `HTTP ${res.status}` };
}

export async function http<T>(path: string, init: HttpInit = {}): Promise<T> {
  const { body, authRedirect = true, headers: given, method: givenMethod, ...rest } = init;
  const method = (givenMethod ?? "GET").toUpperCase();
  const headers = new Headers(given);
  headers.set("Accept", "application/json");
  if (body !== undefined) headers.set("Content-Type", "application/json");
  if (!READ_ONLY.includes(method)) {
    const token = csrfToken();
    if (token) headers.set("X-CSRFToken", token);
  }

  let res: Response;
  try {
    res = await fetch(`/api${path}`, {
      ...rest,
      method,
      headers,
      credentials: "same-origin",
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new BackendDown();
  }

  // A 502/504 from a proxy in front of the backend means it is down. One with a problem+json
  // body comes from the backend itself (e.g. the agent's 504 for a node without parameter
  // services) and carries a message worth showing.
  const fromBackend = (res.headers.get("Content-Type") ?? "").includes("problem+json");
  if ((res.status === 502 || res.status === 504) && !fromBackend) throw new BackendDown();
  if (!res.ok) {
    if (res.status === 401 && authRedirect) toLogin();
    throw new ApiError(res.status, await problemOf(res));
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}
