# Drop the Bug-Fab frontend bundle here

The Next.js layout (`src/app/layout.tsx`) loads two files from this
directory at runtime:

- `bug-fab.js`
- `vendor/html2canvas.min.js`

We deliberately do **not** commit copies of these files into the example
— the bundle is maintained at `repo/static/` and pinned upstream. To
fetch a working copy before running the demo:

```bash
# From the project root (this directory's parent's parent):
mkdir -p public/bug-fab/vendor
cp ../../static/bug-fab.js public/bug-fab/
cp ../../static/vendor/html2canvas.min.js public/bug-fab/vendor/
```

Or, if you are running the example from outside the Bug-Fab monorepo,
fetch them from the published location documented in
[`repo/static/README.md`](../../../../static/README.md).

`.gitignore` excludes `public/bug-fab/bug-fab.js` and
`public/bug-fab/vendor/` so a local copy never accidentally ships.
