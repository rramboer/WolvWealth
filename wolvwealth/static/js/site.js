// Shared site behavior: mobile menu toggle and translucent header on scroll.
"use strict";

document.addEventListener("DOMContentLoaded", () => {
  const toggleButton = document.getElementById("toggleButton");
  if (toggleButton) {
    toggleButton.addEventListener("click", () => {
      const navMenu = document.getElementById("navbar-default");
      if (navMenu) {
        navMenu.classList.toggle("hidden");
      }
    });
  }

  const header = document.querySelector("header");
  if (header) {
    window.addEventListener("scroll", () => {
      header.classList.add("transition", "duration-300", "ease-out");
      if (window.scrollY > 0) {
        header.classList.add("bg-gray-dark/50", "backdrop-blur");
      } else {
        header.classList.remove("bg-gray-dark/50", "backdrop-blur");
      }
    });
  }
});
