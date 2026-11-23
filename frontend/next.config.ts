import type { NextConfig } from "next";

// The browser talks to /api/* on the same origin; Next proxies it to FastAPI.
// Same-origin means the camera page also works over HTTPS on a phone (no mixed content).
const BACKEND_URL = process.env.BACKEND_URL || "http://127.0.0.1:8000";

// Hosts allowed to load dev-server resources (HMR). Add your LAN IP to test the camera from a phone:
// ALLOWED_DEV_ORIGINS=192.168.1.20 npm run dev:https
const allowedDevOrigins = ["127.0.0.1", "localhost", ...(process.env.ALLOWED_DEV_ORIGINS || "").split(",").map((s) => s.trim()).filter(Boolean)];

const nextConfig: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  output: "standalone",
  allowedDevOrigins,
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${BACKEND_URL}/api/:path*` }];
  },
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          { key: "Permissions-Policy", value: "camera=(self), microphone=()" },
        ],
      },
    ];
  },
};

export default nextConfig;
