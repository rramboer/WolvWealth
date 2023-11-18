/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./wolvwealth/**/*.{html,js,jsx}"],
  theme: {
    colors: {
      'gray-dark': '#111111',
      'gray': '#333333',
      'white': '#ffffff',
      'yellow': '#ffff6b',
      'blue': '#3a4cb4',
      'green': '#3afd1d',
    },
    fontFamily: {
      sans: ['Roboto', 'sans-serif'],
      mono: ['"Fira Code"', 'monospace'],
    },
    extend: {
      borderRadius: {
        '4xl': '2rem',
      }
    },
  },
  plugins: [],
}

