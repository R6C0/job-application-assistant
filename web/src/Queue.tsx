import { AnimatePresence, motion } from "framer-motion";
import { Briefcase, Inbox, MapPin, Search, SlidersHorizontal } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { api, formatSalary, timeAgo, type Application, type Stage } from "./api";
import { Chip, Empty, ScoreRing, Skeletons } from "./components";

const TABS: { key: Stage; label: string }[] = [
  { key: "awaiting_review", label: "To review" },
  { key: "approved", label: "Approved" },
  { key: "submitted", label: "Submitted" },
  { key: "rejected", label: "Rejected" },
];

type Sort = "score" | "recent";

export function Queue() {
  const [stage, setStage] = useState<Stage>("awaiting_review");
  const [apps, setApps] = useState<Application[] | null>(null);
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<Sort>("score");
  const [cursor, setCursor] = useState(0);
  const searchRef = useRef<HTMLInputElement>(null);
  const navigate = useNavigate();

  useEffect(() => {
    let cancelled = false;
    setApps(null);
    api
      .list(stage, stage === "rejected" ? 60 : 200)
      .then((data) => !cancelled && setApps(data))
      .catch(() => !cancelled && setApps([]));
    return () => {
      cancelled = true;
    };
  }, [stage]);

  const visible = useMemo(() => {
    if (!apps) return [];
    const needle = query.trim().toLowerCase();
    const filtered = needle
      ? apps.filter(
          (a) =>
            a.job.title.toLowerCase().includes(needle) ||
            a.job.company.toLowerCase().includes(needle) ||
            (a.job.location ?? "").toLowerCase().includes(needle),
        )
      : apps;

    return [...filtered].sort((a, b) =>
      sort === "score"
        ? (b.match?.score ?? 0) - (a.match?.score ?? 0)
        : (b.job.posted_at ?? "").localeCompare(a.job.posted_at ?? ""),
    );
  }, [apps, query, sort]);

  // j/k to move, Enter to open, / to search. Reviewing twenty jobs with the
  // mouse is the difference between using this daily and not.
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      const typing =
        document.activeElement instanceof HTMLInputElement ||
        document.activeElement instanceof HTMLTextAreaElement;

      if (event.key === "/" && !typing) {
        event.preventDefault();
        searchRef.current?.focus();
        return;
      }
      if (typing) return;

      if (event.key === "j" || event.key === "ArrowDown") {
        event.preventDefault();
        setCursor((c) => Math.min(c + 1, visible.length - 1));
      } else if (event.key === "k" || event.key === "ArrowUp") {
        event.preventDefault();
        setCursor((c) => Math.max(c - 1, 0));
      } else if (event.key === "Enter" && visible[cursor]) {
        navigate(`/job/${visible[cursor].job.id}`);
      }
    }

    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [visible, cursor, navigate]);

  useEffect(() => setCursor(0), [stage, query, sort]);

  useEffect(() => {
    document.querySelector(".job-card.selected")?.scrollIntoView({
      block: "nearest",
      behavior: "smooth",
    });
  }, [cursor]);

  return (
    <>
      <div
        style={{
          display: "flex",
          gap: 12,
          alignItems: "center",
          margin: "18px 0 20px",
          flexWrap: "wrap",
        }}
      >
        <div className="tabs">
          {TABS.map((tab) => (
            <button
              key={tab.key}
              className={`tab ${stage === tab.key ? "active" : ""}`}
              onClick={() => setStage(tab.key)}
            >
              {stage === tab.key && (
                <motion.span
                  layoutId="tab-bg"
                  className="tab-bg"
                  transition={{ type: "spring", stiffness: 380, damping: 32 }}
                />
              )}
              {tab.label}
            </button>
          ))}
        </div>

        <div className="search">
          <Search size={15} color="#5f6878" />
          <input
            ref={searchRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Filter by title, company or place"
          />
          <span className="kbd">/</span>
        </div>

        <button
          className="btn sm ghost"
          onClick={() => setSort(sort === "score" ? "recent" : "score")}
          title="Change sort order"
        >
          <SlidersHorizontal size={14} />
          {sort === "score" ? "Best match" : "Most recent"}
        </button>
      </div>

      {apps === null && <Skeletons count={4} />}

      {apps !== null && visible.length === 0 && (
        <Empty icon={<Inbox size={30} color="#5f6878" />} title="Nothing here">
          {query
            ? "No job matches that filter."
            : stage === "awaiting_review"
              ? "Press Run to look for new postings."
              : "Nothing at this stage yet."}
        </Empty>
      )}

      <AnimatePresence mode="popLayout">
        {visible.map((app, index) => (
          <motion.div
            key={app.job.id}
            layout
            initial={{ opacity: 0, y: 14 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.98 }}
            transition={{ duration: 0.28, delay: Math.min(index * 0.035, 0.3) }}
          >
            <Link
              to={`/job/${app.job.id}`}
              className={`card job-card ${index === cursor ? "selected" : ""}`}
              onMouseEnter={() => setCursor(index)}
            >
              <div className="job-row">
                <div className="job-main">
                  <h3 className="job-title">{app.job.title}</h3>
                  <div className="job-meta">
                    <Briefcase size={13} />
                    <span>{app.job.company}</span>
                    {app.job.location && (
                      <>
                        <span className="dot" />
                        <MapPin size={13} />
                        <span>{app.job.location}</span>
                      </>
                    )}
                    {formatSalary(app.job) && (
                      <>
                        <span className="dot" />
                        <span>{formatSalary(app.job)}</span>
                      </>
                    )}
                    {timeAgo(app.job.posted_at) && (
                      <>
                        <span className="dot" />
                        <span>{timeAgo(app.job.posted_at)}</span>
                      </>
                    )}
                  </div>

                  <div className="chips">
                    {app.match?.reasons
                      .filter((r) => r.points > 0)
                      .sort((a, b) => b.points - a.points)
                      .slice(0, 3)
                      .map((r) => (
                        <Chip key={r.component}>{r.detail}</Chip>
                      ))}
                    {app.letter && !app.letter.edited_by_human && (
                      <Chip tone="warn">draft not read</Chip>
                    )}
                    {app.stage === "rejected" && app.notes && (
                      <Chip tone="off">{app.notes}</Chip>
                    )}
                  </div>
                </div>

                {app.match && <ScoreRing score={app.match.score} />}
              </div>
            </Link>
          </motion.div>
        ))}
      </AnimatePresence>

      {visible.length > 0 && (
        <p style={{ color: "#5f6878", fontSize: 12.5, marginTop: 18 }}>
          <span className="kbd">j</span> <span className="kbd">k</span> to move,{" "}
          <span className="kbd">enter</span> to open, <span className="kbd">/</span> to
          filter
        </p>
      )}
    </>
  );
}
