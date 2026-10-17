/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./src/app/**/*.{js,ts,jsx,tsx}", "./src/components/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: '#0b2545',
        paper: '#f6f7f9',
        cyan: '#06b6d4',
        teal: '#14b8a6',
        violet: '#8b5cf6',
      },
    },
  },
  plugins: [],
};
