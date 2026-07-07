import type { NextConfig } from "next";

// Static export: the app is fully client-side (it fetches the FastAPI backend at /api), so it
// builds to plain static files in `out/`. On Vercel these are served statically alongside the
// Python serverless function (see ../vercel.json), which sidesteps Next.js framework detection in
// this monorepo layout (Next app in frontend/, Python API in api/ at the repo root).
const nextConfig: NextConfig = {
  output: "export",
  images: { unoptimized: true },
};

export default nextConfig;
