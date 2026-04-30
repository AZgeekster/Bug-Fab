// next.config.js — Bug-Fab Next.js minimal POC.
//
// WHY bodySizeLimit = '11mb': PROTOCOL.md caps screenshots at 10 MiB and
// total request size at 11 MiB (10 MiB PNG + ~1 MiB metadata + multipart
// overhead). Next.js's default server-action body-size limit is 1 MB,
// which would silently 413 a normal high-DPI screenshot before our
// Route Handler ever sees the bytes. The setting also affects formData()
// parsing in Route Handlers under recent Next.js builds.

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
