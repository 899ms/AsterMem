/**
 * Background: App entry point, responsible for mounting the React tree and global resources (fonts, styles).
 * Design intent: DM Mono / Manrope are locally bundled via @fontsource to avoid CDN dependency;
 * CJK falls back to the system font stack—no CJK fonts bundled (size consideration).
 * Key constraint: Style import order is base → components → pages; later files depend on earlier tokens.
 */
import React from "react";
import ReactDOM from "react-dom/client";
import "@fontsource/dm-mono/400.css";
import "@fontsource/dm-mono/500.css";
import "@fontsource/manrope/400.css";
import "@fontsource/manrope/500.css";
import "@fontsource/manrope/600.css";
import "./styles/base.css";
import "./styles/components.css";
import "./styles/pages.css";
import { App } from "./App";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
