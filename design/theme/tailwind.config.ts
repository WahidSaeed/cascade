import type { Config } from "tailwindcss";

/**
 * Cascade theme — identical tokens to the Cascade deck.
 * Fonts are provided by next/font in app/layout.tsx as the CSS variables
 * --font-inter and --font-mono (see globals.css / Appendix A of BUILD.md).
 */
const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        primary: "#0066cc",
        "primary-focus": "#0071e3",
        sky: "#2997ff",
        ink: "#1d1d1f",
        "muted-80": "#333333",
        "muted-48": "#7a7a7a",
        "body-muted": "#cccccc", // secondary text on dark surfaces
        hairline: "#e0e0e0",
        divider: "#f0f0f0",
        canvas: "#ffffff",
        parchment: "#f5f5f7",
        pearl: "#fafafc",
        tile1: "#272729",
        tile2: "#2a2a2c",
        tile3: "#252527",
        ok: "#1a8f3c",
        warn: "#b25000",
        bad: "#b00020",
      },
      fontFamily: {
        sans: ["var(--font-inter)", "system-ui", "-apple-system", "sans-serif"],
        mono: ["var(--font-mono)", "ui-monospace", "Menlo", "monospace"],
      },
      fontWeight: {
        // the deck ladder is 300 / 400 / 600 / 700 — 500 is deliberately absent
      },
      borderRadius: {
        sm: "8px",
        md: "11px",
        lg: "18px",
        pill: "9999px",
      },
      letterSpacing: {
        tight: "-0.02em",   // display headlines ("Apple tight")
        tighter: "-0.035em",// hero
        eyebrow: "0.2em",   // mono uppercase labels
      },
      boxShadow: {
        // the ONLY shadow in the system — reserve for a single focal element
        product: "0 5px 30px 3px rgba(0,0,0,0.22)",
      },
      fontSize: {
        body: ["17px", { lineHeight: "1.47", letterSpacing: "-0.011em" }],
        eyebrow: ["12px", { lineHeight: "1", letterSpacing: "0.2em" }],
      },
      transitionTimingFunction: {
        cascade: "cubic-bezier(0.2, 0.7, 0.2, 1)",
      },
    },
  },
  plugins: [],
};

export default config;
