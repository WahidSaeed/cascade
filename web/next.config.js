/** @type {import('next').NextConfig} */
module.exports = {
  turbopack: { root: __dirname },
  output: "export",
  trailingSlash: true,
  images: { unoptimized: true },
  reactStrictMode: true,
};
