import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";

// The Flask template renders facts the app needs as data attributes on the
// mount point (see wolvwealth/views/optimizer.py).
const mount = document.getElementById("reactEntry");
const data = mount.dataset;
const config = {
  csrfToken: data.csrfToken,
  firstPriceDate: data.firstPriceDate,
  lastPriceDate: data.lastPriceDate,
  universeSize: Number(data.universeSize),
  usesRemaining: Number(data.usesRemaining),
  maxRebalances: Number(data.maxRebalances),
  maxUniverseSize: Number(data.maxUniverseSize),
};

createRoot(mount).render(
  <StrictMode>
    <App config={config} />
  </StrictMode>,
);
