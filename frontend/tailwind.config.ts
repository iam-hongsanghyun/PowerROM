import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
    "./lib/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        // PLANiT brand palette (planit_brand_kit/planit_tokens.json).
        navy: {
          DEFAULT: "#0C356A",
          600: "#124a8f",
          700: "#0f3f7d",
          800: "#0C356A",
          900: "#082749",
        },
        brand: "#0174BE", // bright blue accent
        gold: "#FFC436", // golden yellow highlight (use sparingly)
        cream: "#FFF0CE", // pale cream soft background
        warmgrey: "#8D8D8D",
        lightgrey: "#D3D3D3",
        // Rebrand the app's accent scale (`sky-*`) onto PLANiT bright blue, so every
        // active/link/highlight blue picks up the brand accent without per-file edits.
        sky: {
          50: "#eaf4fb",
          100: "#d0e6f5",
          200: "#a1ceeb",
          300: "#6fb4df",
          400: "#3897d0",
          500: "#0174BE",
          600: "#015e9b",
          700: "#014876",
          800: "#0C356A",
          900: "#08284f",
        },
      },
      fontFamily: {
        sans: ["var(--font-roboto)", "var(--font-noto-kr)", "Arial", "Helvetica", "sans-serif"],
        display: ["var(--font-roboto-condensed)", "var(--font-roboto)", "Arial", "sans-serif"],
        mono: ["var(--font-roboto-mono)", "monospace"],
      },
    },
  },
  plugins: [],
};

export default config;
