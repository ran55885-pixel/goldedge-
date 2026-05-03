import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        gold: {
          50: "#FBF6E4",
          100: "#F4E7B5",
          200: "#E8C866",
          300: "#D4AF37",
          400: "#B8941F",
          500: "#A8861F",
          600: "#7A5F12",
        },
        ink: {
          900: "#0A0B0C",
          800: "#0F1113",
          700: "#16181B",
          600: "#1A1D20",
          500: "#22262A",
          400: "#2C3137",
        },
        emerald: {
          glow: "#21C383",
        },
      },
      fontFamily: {
        display: ["Georgia", "serif"],
      },
      boxShadow: {
        gold: "0 8px 30px -8px rgba(212,175,55,0.35)",
      },
    },
  },
  plugins: [],
};
export default config;
