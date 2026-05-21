// Optional GitHub Issues sync.
//
// CRITICAL: GitHub sync failures MUST NOT cause the intake response to be
// non-2xx. Log the failure and return null. See PROTOCOL.md
// § "Failure modes that MUST NOT yield non-2xx".

import type { GitHubSyncOptions, BugReportDetail } from './types.js';

export interface GitHubIssueRef {
  url: string;
  number: number;
}

/**
 * Best-effort sync to GitHub Issues. Returns null on any failure.
 * Errors are written to console.warn for operator visibility.
 */
export async function syncToGitHubIssues(
  detail: BugReportDetail,
  opts: GitHubSyncOptions
): Promise<GitHubIssueRef | null> {
  if (!opts.enabled) return null;

  const apiBase = opts.apiBase ?? 'https://api.github.com';
  const url = `${apiBase}/repos/${opts.repo}/issues`;

  const body = {
    title: `[bug-fab] ${detail.title}`,
    body: buildIssueBody(detail),
    labels: opts.labels ?? ['bug-fab']
  };

  try {
    const resp = await fetch(url, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${opts.pat}`,
        Accept: 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
        'Content-Type': 'application/json'
      },
      body: JSON.stringify(body)
    });

    if (!resp.ok) {
      console.warn(`[bug-fab] GitHub sync failed: ${resp.status} ${resp.statusText}`);
      return null;
    }

    const issue = (await resp.json()) as { html_url?: string; number?: number };
    if (!issue.html_url || typeof issue.number !== 'number') {
      console.warn('[bug-fab] GitHub sync returned malformed issue payload');
      return null;
    }
    return { url: issue.html_url, number: issue.number };
  } catch (err) {
    console.warn(`[bug-fab] GitHub sync error: ${err instanceof Error ? err.message : String(err)}`);
    return null;
  }
}

/**
 * Best-effort transition of the linked GitHub issue when status updates.
 * fixed/closed → close issue; open/investigating → reopen.
 */
export async function syncStatusToGitHub(
  detail: BugReportDetail,
  opts: GitHubSyncOptions
): Promise<void> {
  if (!opts.enabled || !detail.github_issue_number) return;
  const apiBase = opts.apiBase ?? 'https://api.github.com';
  const url = `${apiBase}/repos/${opts.repo}/issues/${detail.github_issue_number}`;
  const state = detail.status === 'fixed' || detail.status === 'closed' ? 'closed' : 'open';
  try {
    const resp = await fetch(url, {
      method: 'PATCH',
      headers: {
        Authorization: `Bearer ${opts.pat}`,
        Accept: 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({ state })
    });
    if (!resp.ok) {
      console.warn(`[bug-fab] GitHub status sync failed: ${resp.status} ${resp.statusText}`);
    }
  } catch (err) {
    console.warn(`[bug-fab] GitHub status sync error: ${err instanceof Error ? err.message : String(err)}`);
  }
}

function buildIssueBody(detail: BugReportDetail): string {
  const ctx = detail.context ?? {};
  const lines: string[] = [
    `**Bug-Fab report:** \`${detail.id}\``,
    `**Severity:** ${detail.severity}`,
    `**Status:** ${detail.status}`,
    `**Reported at:** ${detail.created_at}`,
    ''
  ];
  if (detail.description) {
    lines.push('### Description', detail.description, '');
  }
  if (detail.expected_behavior) {
    lines.push('### Expected behavior', detail.expected_behavior, '');
  }
  if (ctx.url) {
    lines.push(`**Page:** ${ctx.url}`);
  }
  if (ctx.app_version) {
    lines.push(`**App version:** ${ctx.app_version}`);
  }
  if (detail.environment) {
    lines.push(`**Environment:** ${detail.environment}`);
  }
  return lines.join('\n');
}
