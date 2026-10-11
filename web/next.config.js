/* eslint-disable @typescript-eslint/no-require-imports -- Next loads this file as CommonJS */
const fs = require("node:fs");
const path = require("node:path");

// The artifact editor's Monaco files are served by the app itself (no CDN on stage or behind CloudFront):
// copied from node_modules on every dev / build start, into public/monaco/vs (git-ignored).
const monacoSrc = path.join(__dirname, "node_modules", "monaco-editor", "min", "vs");
const monacoDst = path.join(__dirname, "public", "monaco", "vs");
if (fs.existsSync(monacoSrc) && !fs.existsSync(path.join(monacoDst, "loader.js"))) {
  fs.cpSync(monacoSrc, monacoDst, { recursive: true });
}

/** @type {import('next').NextConfig} */
module.exports = {
  turbopack: { root: __dirname },
  output: "export",
  trailingSlash: true,
  images: { unoptimized: true },
  reactStrictMode: true,
};
