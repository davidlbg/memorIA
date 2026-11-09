import React from "react";
import ReactDOM from "react-dom/client";
import "@fontsource-variable/catamaran";
import "@fontsource-variable/epilogue";
import "../styles/app.css";
import { App } from "./App";

const root = document.getElementById("root");
if (!root) throw new Error("Root element missing");
document.documentElement.dataset.theme =
  localStorage.getItem("memory-theme") ?? "system";
ReactDOM.createRoot(root).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
