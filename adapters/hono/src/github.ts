// Best-effort GitHub Issues sync — fetch-based so it runs unchanged on
// Cloudflare Workers, Bun, Deno, Vercel Edge, and Node.
//
// Per docs/PROTOCOL.md: GitHub sync failures MUST log server-side and
// MUST NOT cause the intake response to be non-2xx. The intake handler
// returns `github_issue_url: null` on failure.

import type { Severity, Status } from './types.js'

export interface GitHubSyncOptions {
  pat: string
  /** "owner/repo" */
  repo: string
  /** Default "https://api.github.com" — overridable for GHES. */
  apiBase?: string
}

const SEVERITY_LABEL_COLORS: Record<Severity, string> = {
  low: 'c5def5',
  medium: 'fbca04',
  high: 'e4e669',
  critical: 'b60205',
}

function ghHeaders(pat: string): Record<string, string> {
  return {
    Authorization: `Bearer ${pat}`,
    Accept: 'application/vnd.github+json',
    'Content-Type': 'application/json',
    'User-Agent': 'bug-fab-hono/0.1',
    'X-GitHub-Api-Version': '2022-11-28',
  }
}

async function ensureLabel(
  apiBase: string,
  pat: string,
  repo: string,
  name: string,
  color: string,
  log?: (msg: string) => void,
): Promise<void> {
  try {
    // 201 = created; 422 = already exists. Both acceptable.
    await fetch(`${apiBase}/repos/${repo}/labels`, {
      method: 'POST',
      headers: ghHeaders(pat),
      body: JSON.stringify({ name, color }),
    })
  } catch (err) {
    log?.(`[bug-fab] GitHub label ensure failed for "${name}": ${String(err)}`)
  }
}

export interface CreateIssueInput {
  id: string
  title: string
  description: string
  severity: Severity
  tags?: string[]
  context?: { url?: string; environment?: string; app_version?: string }
}

export async function createGitHubIssue(
  opts: GitHubSyncOptions,
  report: CreateIssueInput,
  log?: (msg: string) => void,
): Promise<{ issueUrl: string; issueNumber: number } | null> {
  const base = opts.apiBase ?? 'https://api.github.com'

  try {
    const severityLabel = `severity:${report.severity}`
    await ensureLabel(
      base,
      opts.pat,
      opts.repo,
      severityLabel,
      SEVERITY_LABEL_COLORS[report.severity],
      log,
    )

    const contextLines: string[] = []
    if (report.context?.url) contextLines.push(`**URL:** ${report.context.url}`)
    if (report.context?.environment) {
      contextLines.push(`**Environment:** ${report.context.environment}`)
    }
    if (report.context?.app_version) {
      contextLines.push(`**App version:** ${report.context.app_version}`)
    }

    const body = [
      `**Bug-Fab report ID:** \`${report.id}\``,
      '',
      report.description,
      ...(contextLines.length > 0 ? ['', '---', ...contextLines] : []),
    ].join('\n')

    const labels = [severityLabel, 'bug-fab', ...(report.tags ?? [])]

    const res = await fetch(`${base}/repos/${opts.repo}/issues`, {
      method: 'POST',
      headers: ghHeaders(opts.pat),
      body: JSON.stringify({ title: report.title, body, labels }),
    })

    if (!res.ok) {
      log?.(`[bug-fab] GitHub issue creation returned ${res.status}`)
      return null
    }

    const issue = (await res.json()) as { html_url: string; number: number }
    return { issueUrl: issue.html_url, issueNumber: issue.number }
  } catch (err) {
    log?.(`[bug-fab] GitHub issue creation failed: ${String(err)}`)
    return null
  }
}

export function toGitHubState(status: Status): 'open' | 'closed' {
  return status === 'fixed' || status === 'closed' ? 'closed' : 'open'
}

export async function syncGitHubIssueState(
  opts: GitHubSyncOptions,
  issueNumber: number,
  newStatus: Status,
  log?: (msg: string) => void,
): Promise<void> {
  const base = opts.apiBase ?? 'https://api.github.com'
  const state = toGitHubState(newStatus)

  try {
    const res = await fetch(`${base}/repos/${opts.repo}/issues/${issueNumber}`, {
      method: 'PATCH',
      headers: ghHeaders(opts.pat),
      body: JSON.stringify({ state }),
    })
    if (!res.ok) {
      log?.(`[bug-fab] GitHub issue ${issueNumber} state sync returned ${res.status}`)
    }
  } catch (err) {
    log?.(`[bug-fab] GitHub issue state sync failed: ${String(err)}`)
  }
}
