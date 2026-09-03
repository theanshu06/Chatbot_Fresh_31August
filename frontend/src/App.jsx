import { useEffect, useRef, useState } from "react";
import { checkHealth } from "./api.js";
import PdfPanel from "./components/PdfPanel.jsx";
import WebsitePanel from "./components/WebsitePanel.jsx";
import DatabasePanel from "./components/DatabasePanel.jsx";
import RelationshipsPanel from "./components/RelationshipsPanel.jsx";
import ChatPanel from "./components/ChatPanel.jsx";
import ShowIngested from "./components/ShowIngested.jsx";

export default function App() {
  const [health, setHealth] = useState(null);
  const showRef = useRef(null);
  const relRef = useRef(null);

  useEffect(() => {
    checkHealth()
      .then(() => setHealth("ok"))
      .catch(() => setHealth("down"));
  }, []);

  function refreshAll() {
    showRef.current?.refresh();
    relRef.current?.refresh();
  }

  return (
    <div className="app">
      <header className="app-header">
        <h1>Ingestion Pipeline Tester</h1>
        <span className={`status status-${health}`}>
          backend: {health === "ok" ? "connected" : health === "down" ? "unreachable" : "checking…"}
        </span>
      </header>

      <div className="panel-grid">
        <PdfPanel onIngested={refreshAll} />
        <WebsitePanel onIngested={refreshAll} />
        <DatabasePanel onIngested={refreshAll} />
      </div>

      <RelationshipsPanel ref={relRef} />

      <ChatPanel />

      <ShowIngested ref={showRef} />
    </div>
  );
}
