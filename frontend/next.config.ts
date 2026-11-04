import type { NextConfig } from "next";

const apiOrigin = process.env.MULTEAGENT_API_ORIGIN ;
// the browser reaches the API only through this rewrite (same origin → first-party session cookies). Its
// proxy gives up on a request after 30 s by default; one model call may take LLM_TIMEOUT_SECONDS (300 s)
const API_PROXY_TIMEOUT_MS = 300_000;

const nextConfig: NextConfig = {
  allowedDevOrigins: ['192.168.88.36'],
  experimental: { proxyTimeout: API_PROXY_TIMEOUT_MS },
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
