import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const config = [
  ...nextVitals,
  ...nextTs,
  {
    ignores: ["out/**", ".next/**", "public/monaco/**", "lib/api-types.ts", "playwright-report/**", "test-results/**",
      "next-env.d.ts", "node_modules/**"],
  },
];

export default config;
