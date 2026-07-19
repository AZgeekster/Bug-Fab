// next.config.js — Bug-Fab Next.js minimal POC.
//
// WHY bodySizeLimit = '11mb': PROTOCOL.md caps screenshots at 10 MiB and
// total request size at 11 MiB (10 MiB PNG + ~1 MiB metadata + multipart
// overhead). Next.js's default server-action body-size limit is 1 MB,
// which would silently 413 a screenshot submitted through a Server
// Action. NOTE: this knob bounds Server Actions ONLY — it does NOT
// affect formData() parsing in App Router Route Handlers, which have no
// framework body limit. The intake handler's own 10 MiB check is what
// enforces the cap there; do not remove that guard on the strength of
// this setting.

/** @type {import('next').NextConfig} */
const nextConfig = {
  experimental: {
    serverActions: {
      bodySizeLimit: '11mb',
    },
  },
  // App Router Route Handlers ignore the legacy `api.bodyParser` knob,
  // but we keep this block documented for Pages Router consumers who
  // copy the example.
  // api: { bodyParser: { sizeLimit: '11mb' } },
}

module.exports = nextConfig
