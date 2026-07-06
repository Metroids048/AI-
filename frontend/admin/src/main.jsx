import React from "react";
import ReactDOM from "react-dom/client";

import { PaperConsole } from "./pages/PaperConsole";
import "./styles.css";

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <PaperConsole />
  </React.StrictMode>,
);
