/** @type {import('tailwindcss').Config} */
export default {
  darkMode: 'class',
  content: [
    "./index.html",
    "./src/**/*.{vue,js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      fontFamily: {
        sans: ['"Plus Jakarta Sans"', 'system-ui', '-apple-system', 'sans-serif'],
        mono: ['"JetBrains Mono"', 'monospace'],
      },
      colors: {
        background: '#0d0e10',
        surface: {
          DEFAULT: '#131518',
          raised: '#181b20',
          hover: '#1e2228',
          modal: '#1c2026',
        },
        border: {
          subtle: '#20242c',
          DEFAULT: '#262a33',
          strong: '#353b47',
        },
        text: {
          primary: '#f1f3f7',
          secondary: '#9ba3af',
          muted: '#636b77',
        },
        brand: {
          amber: '#f59e0b',
          emerald: '#10b981',
          sky: '#0ea5e9',
          purple: '#a855f7',
          rose: '#f43f5e',
        }
      }
    },
  },
  plugins: [],
}
