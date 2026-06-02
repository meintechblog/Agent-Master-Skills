// GitHub-Modul (webapp-scaffold --with-github) — offene Issues + PRs des EIGENEN Repos.
// Repo-parametrisiert via ENV; bewusst minimal (single-repo, read-only) — KEINE Triage/
// Whitelist/Delivery (Multi-Repo-Orchestrierung, gehört nicht in eine einzelne App).
//
// ENV:
//   GITHUB_REPO   "owner/name"  (Pflicht; ohne → leeres Ergebnis mit Hinweis)
//   GITHUB_TOKEN  PAT mit repo-Scope (Pflicht für PRIVATE Repos; public geht ohne, 60 req/h)
//
// Nutzt die GitHub-REST-API (kein `gh`-CLI nötig → läuft auch auf einem nackten LXC).

export type GithubItem = {
  number: number;
  title: string;
  author: string;
  url: string;
  created_at: string;
};
export type GithubResult = {
  repo: string | null;
  issues: GithubItem[];
  prs: GithubItem[];
  fetched_at: string;
  error: string | null;
};

const API = "https://api.github.com";

export async function fetchOpenItems(
  repo: string | undefined = process.env.GITHUB_REPO,
  token: string | undefined = process.env.GITHUB_TOKEN,
): Promise<GithubResult> {
  const fetched_at = new Date().toISOString();
  const empty = (error: string | null): GithubResult => ({ repo: repo || null, issues: [], prs: [], fetched_at, error });
  if (!repo || !/^[^/]+\/[^/]+$/.test(repo)) {
    return empty("GITHUB_REPO nicht gesetzt (erwartet \"owner/name\").");
  }
  const headers: Record<string, string> = {
    Accept: "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": "webapp-scaffold-github-module",
  };
  if (token) headers.Authorization = `Bearer ${token}`;
  try {
    // Die Issues-API liefert Issues UND PRs; PRs erkennt man am `pull_request`-Feld.
    const res = await fetch(`${API}/repos/${repo}/issues?state=open&per_page=50&sort=created&direction=desc`, {
      headers,
      cache: "no-store",
    });
    if (res.status === 404) return empty("Repo nicht gefunden oder kein Zugriff (privat → GITHUB_TOKEN nötig?).");
    if (res.status === 401 || res.status === 403) {
      const rl = res.headers.get("x-ratelimit-remaining");
      return empty(rl === "0" ? "GitHub-Rate-Limit erreicht (Token setzen erhöht das Limit)." : "Kein Zugriff (Token ungültig/fehlt?).");
    }
    if (!res.ok) return empty(`GitHub-API-Fehler: HTTP ${res.status}`);
    const rows = (await res.json()) as Array<{
      number: number; title: string; html_url: string; created_at: string;
      user?: { login?: string }; pull_request?: unknown;
    }>;
    const issues: GithubItem[] = [];
    const prs: GithubItem[] = [];
    for (const r of rows) {
      const item: GithubItem = {
        number: r.number,
        title: r.title,
        author: r.user?.login || "unknown",
        url: r.html_url,
        created_at: r.created_at,
      };
      (r.pull_request ? prs : issues).push(item);
    }
    return { repo, issues, prs, fetched_at, error: null };
  } catch (e) {
    return empty(`Netzwerkfehler: ${e instanceof Error ? e.message : String(e)}`);
  }
}
