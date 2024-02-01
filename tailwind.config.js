/** @type {import('tailwindcss').Config} */

module.exports = {
  content: ["./wolvwealth/**/*.{html,js,jsx}"],
  theme: {
    colors: {
      'gray-dark': '#111111',
      'gray': '#222222',
      'white': '#ffffff',
      'yellow': '#ffff6b',
      'blue': '#2F52E0',
      'cyan': '#67e8f9',
      'green': '#42d36e',
      'red': '#ff715b',
      'black': '#000000',
    },
    fontFamily: {
      sans: ['-apple-system', 'BlinkMacSystemFont', 'Helvetica Neue', 'sans-serif'],
    },
    extend: {
      borderRadius: {
        '4xl': '2rem',
      },
      backgroundColor: {
        'transparent': 'transparent',
      },
    },
  },
  plugins: [],
}

