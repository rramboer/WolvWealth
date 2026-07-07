import js from "@eslint/js";
import react from "eslint-plugin-react";
import reactHooks from "eslint-plugin-react-hooks";
import globals from "globals";

export default [
  {
    ignores: ["node_modules/**", "wolvwealth/static/**", ".venv/**", "var/**"],
  },
  js.configs.recommended,
  {
    files: ["*.config.js"],
    languageOptions: {
      globals: globals.node,
    },
  },
  {
    files: ["wolvwealth/js/**/*.{js,jsx}"],
    plugins: {
      react,
      "react-hooks": reactHooks,
    },
    languageOptions: {
      ecmaVersion: 2024,
      sourceType: "module",
      parserOptions: {
        ecmaFeatures: { jsx: true },
      },
      globals: globals.browser,
    },
    settings: {
      react: { version: "detect" },
    },
    rules: {
      ...react.configs.flat.recommended.rules,
      ...reactHooks.configs.recommended.rules,
      // Vite's automatic JSX runtime does not require React in scope.
      "react/react-in-jsx-scope": "off",
    },
  },
];
