/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  output: "standalone", // a self-contained server for the container image (frontend/Dockerfile)
  // The browser talks to /api; Next forwards to the FastAPI service, so there is no CORS surface.
  async rewrites() {
    const api = process.env.PROVENANT_API_URL ?? "http://127.0.0.1:8000";
    return [{ source: "/api/:path*", destination: `${api}/:path*` }];
  },
};

export default nextConfig;
