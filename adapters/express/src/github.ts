// Best-effort GitHub Issues sync.
//
// Per the protocol spec (PROTOCOL.md § "Failure modes that MUST NOT yield non-2xx"),
// a GitHub outage MUST NOT cause the intake response to be non-2xx. Every
// function here returns null / void on failure and logs through the supplied
// logger.
//
// Uses the global `fetch` API (Node 20+).

import type { Severity, Status, Logger } from './types.js'

export interface GitHubSyncOptions {
  pat:     string
  repo:    string             // "owner/repo"
  apiBase?: string            // default "https://api.github.com"
}

const SEVERITY_LABEL_COLORS: Record<Severity, string> = {
  low:      'c5def5',
  medium:   'fbca04',
  high:     'e4e669',
  critical: 'b60205',
}

function makeHeaders(pat: string): Record<string, string> {
  return {
    'Authorization':        `Bearer ${pat}`,
    'Accept':               'application/vnd.github+json',
    'Content-Type':         'application/json',
    'User-Agent':           'bug-fab-express/0.1',
    'X-GitHub-Api-Version': '2022-11-28',
  }
}

async function ensureLabel(
  apiBase: string,
  pat:     string,
  repo:    string,
  name:    string,
  color:   string,
  log?:    Logger,
): Promise<void> {
  try {
    // 201 = created, 422 = already exists — both are fine for our purposes.
    // We do not check the result; this is purely a "make sure the label exists" call.
    await fetch(`${apiBase}/repos/${repo}/labels`, {
      method:  'POST',
      headers: makeHeaders(pat),
      body:    JSON.stringify({ name, color }),
    })
  } catch (err) {
    log?.warn(`[bug-fab-express] GitHub ensureLabel("${name}") failed`, err)
  }
}

export interface CreateIssueInput {
  id:          string
  title:       string
  description: string
  severity:    Severity
  tags?:       string[]
  context?: {
    url?:         string
    environment?: string
    app_version?: string
  }
}

export async function createGitHubIssue(
  opts:  GitHubSyncOptions,
  input: CreateIssueInput,
  log?:  Logger,
): Promise<{ issueUrl: string; issueNumber: number } | null> {
  const base = opts.apiBase ?? 'https://api.github.com'

  try {
    const severityLabel = `severity:${input.severity}`
    await ensureLabel(base, opts.pat, opts.repo, severityLabel, SEVERITY_LABEL_COLORS[input.severity], log)

    const contextLines: string[] = []
    if (input.context?.url)         contextLines.push(`**URL:** ${input.context.url}`)
    if (input.context?.environment) contextLines.push(`**Environment:** ${input.context.environment}`)
    if (input.context?.app_version) contextLines.push(`**App version:** ${input.context.app_version}`)

    const body = [
      `**Bug-Fab report ID:** \`${input.id}\``,
      '',
      input.description,
      ...(contextLines.length > 0 ? ['', '---', ...contextLines] : []),
    ].join('\n')

    const labels = [severityLabel, 'bug-fab', ...(input.tags ?? [])]

    const res = await fetch(`${base}/repos/${opts.repo}/issues`, {
      method:  'POST',
      headers: makeHeaders(opts.pat),
      body:    JSON.stringify({ title: input.title, body, labels }),
    })

    if (!res.ok) {
      log?.warn(`[bug-fab-express] GitHub issue creation returned HTTP ${res.status}`)
      return null
    }

    const issue = (await res.json()) as { html_url: string; number: number }
    return { issueUrl: issue.html_url, issueNumber: issue.number }
  } catch (err) {
    log?.warn('[bug-fab-express] GitHub issue creation failed', err)
    return null
  }
}

export function toGitHubState(status: Status): 'open' | 'closed' {
  return status === 'fixed' || status === 'closed' ? 'closed' : 'open'
}

export async function syncGitHubIssueState(
  opts:        GitHubSyncOptions,
  issueNumber: number,
  newStatus:   Status,
  log?:        Logger,
): Promise<void> {
  const base  = opts.apiBase ?? 'https://api.github.com'
  const state = toGitHubState(newStatus)

  try {
    const res = await fetch(`${base}/repos/${opts.repo}/issues/${issueNumber}`, {
      method:  'PATCH',
      headers: makeHeaders(opts.pat),
      body:    JSON.stringify({ state }),
    })
    if (!res.ok) {
      log?.warn(`[bug-fab-express] GitHub issue ${issueNumber} state sync HTTP ${res.status}`)
    }
  } catch (err) {
    log?.warn(`[bug-fab-express] GitHub issue ${issueNumber} state sync failed`, err)
  }
}
