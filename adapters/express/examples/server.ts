// examples/server.ts — minimal boot-able Express app for cross-stack
// conformance runs and local exploration.
//
// Run directly with `tsx` (no build step required):
//
//   npx tsx examples/server.ts
//
// Then either point the Bug-Fab pytest conformance plugin at it:
//
//   pytest --bug-fab-conformance --base-url=http://localhost:3000/admin/bug-reports
//
// or open http://localhost:3000/admin/bug-reports in a browser to poke
// the (empty) viewer.
//
// Override the listen port via `PORT` and the on-disk storage dir via
// `BUG_FAB_STORAGE_DIR`. No other configuration is read from the
// environment — this is intentionally a thin harness.

import express from 'express'

import { createBugFabRouter, FileStorage } from '../src/index.js'

const PORT = Number.parseInt(process.env.PORT ?? '3000', 10)
const STORAGE_DIR = process.env.BUG_FAB_STORAGE_DIR ?? './var/bug_fab_example'
const MOUNT_PATH = '/admin/bug-reports'

const app = express()
const storage = new FileStorage({ storageDir: STORAGE_DIR })

app.use(MOUNT_PATH, createBugFabRouter({ storage }))

app.listen(PORT, () => {
  // eslint-disable-next-line no-console
  console.log(`Bug-Fab example server listening at http://localhost:${PORT}${MOUNT_PATH}`)
})
