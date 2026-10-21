/** @type {import('next').NextConfig} */
const BACKEND_URL = process.env.BACKEND_URL || "http://127.0.0.1:8000";

const pages = process.env.NEXT_PUBLIC_STATIC_DEMO === "true";
const nextConfig = pages ? {
  output: "export",
  trailingSlash: true,
  basePath: process.env.NEXT_PUBLIC_BASE_PATH || "/museum_agent",
  images: {unoptimized: true},
} : {
  experimental: {
    // LLM 问答可能超过默认 30s 代理超时，放宽到 3 分钟
    proxyTimeout: 180000,
  },
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${BACKEND_URL}/api/:path*` },
      { source: "/healthz", destination: `${BACKEND_URL}/healthz` },
      { source: "/metrics", destination: `${BACKEND_URL}/metrics` },
    ];
  },
};

export default nextConfig;
