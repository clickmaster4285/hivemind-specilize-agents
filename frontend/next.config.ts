import type { NextConfig } from "next";

const apiOrigin = process.env.MULTEAGENT_API_ORIGIN ;
// the browser reaches the API only through this rewrite (same origin → first-party session cookies). Its
// proxy gives up on a request after 30 s by default; one model call may take LLM_TIMEOUT_SECONDS (300 s)
const API_PROXY_TIMEOUT_MS = 300_000;

const nextConfig: NextConfig = {
  allowedDevOrigins: ['192.168.88.36'],
  experimental: { proxyTimeout: API_PROXY_TIMEOUT_MS },
  // L16 S4: the pages' other security headers (the CSP itself comes from src/proxy.ts with its nonce). /api is
  // left out: the API sets the same ones on its own responses (web/headers.py)
  async headers() {
    return [
      {
        source: "/((?!api/).*)",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "no-referrer" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
        ],
      },
    ];
  },
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${apiOrigin}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
