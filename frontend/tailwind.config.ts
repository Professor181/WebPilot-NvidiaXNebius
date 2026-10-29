import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        obsidian: { 950: "#070B14", 900: "#0B1120", 850: "#0F172A" },
        alexa: { cyan: "#00CAFF", indigo: "#6366F1" },
        trust: "#10B981",
        caution: "#F59E0B",
        danger: "#F43F5E",
      },
      fontFamily: {
        sans: ["Inter", "Segoe UI", "Roboto", "Helvetica Neue", "Arial", "sans-serif"],
        mono: ["JetBrains Mono", "Cascadia Code", "Consolas", "ui-monospace", "monospace"],
      },
      boxShadow: {
        glow: "0 0 40px -8px rgba(0, 202, 255, 0.45)",
        amber: "0 0 50px -12px rgba(245, 158, 11, 0.55)",
        emerald: "0 0 40px -12px rgba(16, 185, 129, 0.5)",
      },
      keyframes: {
        "orb-spin": { to: { transform: "rotate(360deg)" } },
        "orb-pulse": {
          "0%, 100%": { transform: "scale(1)", opacity: "0.9" },
          "50%": { transform: "scale(1.12)", opacity: "1" },
        },
        "fade-up": {
          from: { opacity: "0", transform: "translateY(6px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
        shimmer: { "100%": { transform: "translateX(100%)" } },
      },
      animation: {
        "orb-spin": "orb-spin 6s linear infinite",
        "orb-spin-fast": "orb-spin 1.4s linear infinite",
        "orb-pulse": "orb-pulse 1.6s ease-in-out infinite",
        "fade-up": "fade-up 0.35s ease-out both",
        shimmer: "shimmer 1.6s infinite",
      },
    },
  },
  plugins: [],
};

export default config;
