/** @type {import('tailwindcss').Config} */

module.exports = {
  content: ["./wolvwealth/web/templates/**/*.html"],
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
      mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'Consolas', 'monospace'],
    },
    extend: {
      // `colors` above replaces Tailwind's whole palette, so the utilities the
      // templates still need from the default set have to be re-declared here.
      backgroundColor: {
        'transparent': 'transparent',
      },
    },
  },
  plugins: [],
}

