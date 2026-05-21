# bug-fab-hono storage backends

The `IStorage` interface is the single seam between the Hono adapter
and the data layer. Three reference implementations ship with the
package; consumers writing their own only need to satisfy the same
9-method contract (plus the optional `setGitHubIssue` post-save hook).

## When to pick which

| Backend | Where it runs | When to pick it |
|---|---|---|
| `MemoryStorage` | Anywhere | Tests, throwaway POCs. Loses data on every restart. |
| `R2Storage` | Cloudflare Workers | Production on Workers. Metadata in KV (small JSON), screenshots in R2 (large blobs). |
| `KVStorage` | Cloudflare Workers | KV-only POC when no R2 bucket is provisioned. Screenshots are base64-encoded into KV (cap ~25 MiB per value). |

For Bun / Node / Deno deployments where filesystem access is fine, see
the **Filesystem backend (contributor pattern)** below.

## `IStorage` contract

```typescript
interface IStorage {
  saveReport(metadata: StoredMetadata, screenshotBytes: Uint8Array): Promise<string>
  getReport(id: string): Promise<BugReportDetail | null>
  listReports(filters: ListFilters, page: number, pageSize: number):
    Promise<{ items: BugReportSummary[]; total: number; stats: BugReportListStats }>
  getScreenshotBytes(id: string): Promise<Uint8Array | null>
  updateStatus(id: string, newStatus: Status, by: string,
               fixCommit?: string, fixDescription?: string): Promise<BugReportDetail>
  deleteReport(id: string): Promise<void>
  archiveReport(id: string): Promise<void>
  bulkCloseFixed(): Promise<number>
  bulkArchiveClosed(): Promise<number>
  setGitHubIssue?(id: string, issueUrl: string, issueNumber: number): Promise<void>
}
```

Note `getScreenshotBytes` returns `Uint8Array` directly — edge runtimes
have no `node:fs`, so the screenshot is fetched as bytes and the storage
class decides where they came from. This is the single most important
edge-runtime constraint.

## Filesystem backend (contributor pattern)

Bug-Fab does NOT ship a `node:fs` storage backend in this package
because the package targets edge runtimes by default. If you're on Bun
or Node and want a file-based backend, copy this skeleton:

```typescript
import { promises as fs } from 'node:fs'
import * as path from 'node:path'
import type {
  IStorage, StoredMetadata, BugReportDetail,
  BugReportSummary, BugReportListStats, ListFilters, Status,
} from 'bug-fab-hono'

export class FsStorage implements IStorage {
  constructor(private readonly root: string) {}

  async saveReport(meta: StoredMetadata, screenshotBytes: Uint8Array): Promise<string> {
    const id = await this.allocateId()
    const dir = path.join(this.root, id)
    await fs.mkdir(dir, { recursive: true })
    await fs.writeFile(path.join(dir, 'screenshot.png'), screenshotBytes)
    // ...persist metadata.json with the same shape MemoryStorage uses
    return id
  }

  // ...the other 8 methods read / write under `this.root/<id>/`.
  // Mirror the lifecycle bookkeeping in MemoryStorage exactly.
}
```

The Python reference adapter's `bug_fab/storage/file_storage.py` is the
canonical filesystem layout; if your `FsStorage` produces the same
on-disk shape, you stay interoperable with the Python viewer (and vice
versa).

Why isn't this in the package itself? Two reasons:

1. **No `node:` imports.** Adding `node:fs` would break Cloudflare
   Workers / Vercel Edge / Deno Deploy at deploy time.
2. **Persistence is consumer-shaped.** Real production deploys want
   SQLite / Postgres / S3 — the file backend is just one of many. We
   ship the three edge-runtime defaults and document the rest.

## Conformance

Whichever backend you implement, run:

```bash
pip install --pre bug-fab
pytest --bug-fab-conformance --base-url=http://localhost:3000
```

against your running adapter. The test suite calls the eight protocol
endpoints exhaustively and surfaces silent-coercion bugs that hand
testing usually misses.
