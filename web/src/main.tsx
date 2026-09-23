import { motion } from "framer-motion";
import { Briefcase, Loader2, Play } from "lucide-react";
import { StrictMode, useCallback, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  BrowserRouter,
  Link,
  Route,
  Routes,
  useLocation,
} from "react-router-dom";

import { api, type Meta, type Stats } from "./api";
import { ToastHost, useToast } from "./components";
import { Detail } from "./Detail";
import { Queue } from "./Queue";
import "./styles.css";

function TopBar({ stats, onRan }: { stats: Stats | null; onRan: () => void }) {
  const [running, setRunning] = useState(false);
  const toast = useToast();

  // A run takes 30 to 90 seconds, so it is started in the background and
  // polled. Polling beats a websocket here: one client, one machine, and a
  // dropped poll costs nothing.
  const poll = useCallback(async () => {
    const state = await api.runStatus();
    if (state.running) {
      setTimeout(() => void poll(), 1500);
      return;
    }
    setRunning(false);
    if (state.summary) toast(state.summary, state.errors.length ? "err" : "ok");
    onRan();
  }, [onRan, toast]);

  useEffect(() => {
    // If a run was already going when the page loaded, pick it up.
    void api.runStatus().then((state) => {
      if (state.running) {
        setRunning(true);
        void poll();
      }
    });
  }, [poll]);

  async function start() {
    setRunning(true);
    try {
      await api.startRun(false);
      toast("Searching for new postings");
      void poll();
    } catch (error) {
      setRunning(false);
      toast((error as Error).message, "err");
    }
  }

  return (
    <div className="topbar">
      <Link to="/" className="brand">
        <span className="brand-mark">
          <Briefcase size={15} />
        </span>
        jobhunt
      </Link>

      <div className="spacer" />

      {stats && (
        <div className="pills">
          <span className="pill accent">
            <b>{stats.awaiting}</b> to review
          </span>
          {stats.approved > 0 && (
            <span className="pill">
              <b>{stats.approved}</b> approved
            </span>
          )}
          {stats.submitted > 0 && (
            <span className="pill">
              <b>{stats.submitted}</b> submitted
            </span>
          )}
          {stats.average_score !== null && (
            <span className="pill">
              avg <b>{stats.average_score}</b>
            </span>
          )}
        </div>
      )}

      <button className="btn primary" onClick={() => void start()} disabled={running}>
        {running ? (
          <motion.span
            animate={{ rotate: 360 }}
            transition={{ repeat: Infinity, duration: 1, ease: "linear" }}
            style={{ display: "grid", placeItems: "center" }}
          >
            <Loader2 size={15} />
          </motion.span>
        ) : (
          <Play size={15} />
        )}
        {running ? "Searching" : "Run"}
      </button>
    </div>
  );
}

function App() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [meta, setMeta] = useState<Meta | null>(null);
  const location = useLocation();

  const refresh = useCallback(() => {
    void api.stats().then(setStats).catch(() => undefined);
  }, []);

  useEffect(refresh, [refresh, location.pathname]);
  useEffect(() => {
    void api.meta().then(setMeta).catch(() => undefined);
  }, []);

  return (
    <>
      <TopBar stats={stats} onRan={refresh} />
      <div className="shell">
        <Routes>
          <Route path="/" element={<Queue />} />
          <Route path="/job/:id" element={<Detail meta={meta} />} />
          <Route path="*" element={<Queue />} />
        </Routes>
      </div>
    </>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <ToastHost>
        <App />
      </ToastHost>
    </BrowserRouter>
  </StrictMode>,
);
