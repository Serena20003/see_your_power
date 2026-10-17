import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import ModelSources from "./ModelSources";
import "./styles.scss";

if (window.location.pathname === "/model-sources") {
  document.title = "Runway — U.S. outage model sources";
}

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    {window.location.pathname === "/model-sources" ? <ModelSources /> : <App />}
  </React.StrictMode>,
);
