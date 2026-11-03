import type { NextConfig } from "next";

// Production (`npm run build`): a static export in out/, served by the Django backend on the
// same origin as the API and the WebSockets (docs/specs/spec_cockpit.md §4). Development
// (`npm run dev`): Next forwards /api and /login-assets to the backend on COCKPIT_ORIGIN; the
// WebSocket goes to NEXT_PUBLIC_WS_ORIGIN directly (see .env.development).
const COCKPIT_ORIGIN = process.env.COCKPIT_ORIGIN ?? "http://127.0.0.1:8400";
const dev = process.env.NODE_ENV === "development";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  trailingSlash: true, // out/settings/index.html, served as /settings/
  images: { unoptimized: true },
  devIndicators: { position: "bottom-right" },
  ...(dev
    ? {
        skipTrailingSlashRedirect: true,
        async rewrites() {
          return [
            { source: "/api/:path*", destination: `${COCKPIT_ORIGIN}/api/:path*` },
            { source: "/login-assets/:path*", destination: `${COCKPIT_ORIGIN}/login-assets/:path*` },
          ];
        },
      }
    : { output: "export" as const }),
};

export default nextConfig;
