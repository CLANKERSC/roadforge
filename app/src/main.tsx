import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { brand } from "./lib/brand";
import "./styles.css";

document.title = brand.name;
const root = document.documentElement.style;
root.setProperty("--accent", brand.colors.accent);
root.setProperty("--accent2", brand.colors.accent2);
root.setProperty("--bg", brand.colors.bg);
root.setProperty("--panel", brand.colors.panel);
root.setProperty("--text", brand.colors.text);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
