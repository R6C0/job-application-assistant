import { motion } from "framer-motion";
import {
  ArrowLeft,
  Building2,
  Check,
  ExternalLink,
  FileText,
  MousePointerClick,
  Save,
  ShieldCheck,
  SkipForward,
  TriangleAlert,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { api, formatSalary, type Application, type Meta } from "./api";
import { Chip, HighlightedText, ScoreRing, Skeletons, useToast } from "./components";

export function Detail({ meta }: { meta: Meta | null }) {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const toast = useToast();

  const [app, setApp] = useState<Application | null>(null);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api
      .get(id)
      .then((data) => {
        setApp(data);
        setDraft(data.letter?.body ?? "");
      })
      .catch((error) => toast(error.message, "err"));
  }, [id, toast]);

  const dirty = app?.letter ? draft.trim() !== app.letter.body.trim() : false;

  const saveLetter = useCallback(async () => {
    if (!app?.letter || !dirty) return;
    setSaving(true);
    try {
      setApp(await api.saveLetter(app.job.id, draft));
      toast("Letter saved");
    } catch (error) {
      toast((error as Error).message, "err");
    } finally {
      setSaving(false);
    }
  }, [app, draft, dirty, toast]);

  const act = useCallback(
    async (action: "approve" | "skip" | "markSubmitted" | "assist") => {
      if (!app) return;
      setBusy(true);
      try {
        const updated =
          action === "skip"
            ? await api.skip(app.job.id, "not a fit")
            : await api[action](app.job.id);
        setApp(updated);

        if (action === "approve") toast("Approved. Open the form when you are ready.");
        if (action === "skip") {
          toast("Skipped");
          navigate("/");
        }
        if (action === "markSubmitted") {
          toast("Recorded as submitted");
          navigate("/");
        }
        if (action === "assist") toast("Browser opened. Check it, then submit yourself.");
      } catch (error) {
        toast((error as Error).message, "err");
      } finally {
        setBusy(false);
      }
    },
    [app, navigate, toast],
  );

  // Ctrl+S saves, A approves, S skips. The same shortcuts the queue uses.
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      const typing =
        document.activeElement instanceof HTMLInputElement ||
        document.activeElement instanceof HTMLTextAreaElement;

      if ((event.ctrlKey || event.metaKey) && event.key === "s") {
        event.preventDefault();
        void saveLetter();
        return;
      }
      if (typing) return;

      if (event.key === "Escape") navigate("/");
      if (event.key === "a" && app?.stage === "awaiting_review") void act("approve");
      if (event.key === "s" && app?.stage === "awaiting_review") void act("skip");
    }

    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [saveLetter, act, navigate, app?.stage]);

  /** Banned phrases present in the current draft, using the drafter's own list. */
  const banned = useMemo(() => {
    if (!meta) return [];
    const lowered = draft.toLowerCase();
    return meta.banned_phrases.filter((phrase) => lowered.includes(phrase));
  }, [draft, meta]);

  if (!app) return <Skeletons count={3} />;

  const words = draft.trim() ? draft.trim().split(/\s+/).length : 0;
  const maxPoints = Math.max(...(app.match?.reasons.map((r) => r.points) ?? [1]), 1);

  return (
    <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12, margin: "18px 0 16px" }}>
        <Link to="/" className="btn sm ghost">
          <ArrowLeft size={15} /> Queue
        </Link>
        <span className="kbd">esc</span>
      </div>

      <div className="card" style={{ marginBottom: 16 }}>
        <div className="job-row">
          <div className="job-main">
            <h2 style={{ margin: "0 0 6px", fontSize: 22, letterSpacing: "-0.02em" }}>
              {app.job.title}
            </h2>
            <div className="job-meta">
              <Building2 size={14} />
              <span>{app.job.company}</span>
              {app.job.location && (
                <>
                  <span className="dot" />
                  <span>{app.job.location}</span>
                </>
              )}
              {formatSalary(app.job) && (
                <>
                  <span className="dot" />
                  <span>{formatSalary(app.job)}</span>
                </>
              )}
              <span className="dot" />
              <span>via {app.job.source}</span>
            </div>
            <div className="chips">
              <Chip tone={app.stage === "approved" ? "on" : ""}>
                {app.stage.replace(/_/g, " ")}
              </Chip>
              <a
                href={app.job.url}
                target="_blank"
                rel="noreferrer"
                className="chip"
                style={{ textDecoration: "none" }}
              >
                original posting <ExternalLink size={11} />
              </a>
            </div>
          </div>
          {app.match && <ScoreRing score={app.match.score} size={76} />}
        </div>
      </div>

      <div className="detail-grid">
        <div>
          <div className="section-title">Job description</div>
          <div className="card">
            <div className="jd">
              <HighlightedText
                text={app.job.description}
                terms={app.match?.matched_skills ?? []}
              />
            </div>
            {app.match && app.match.matched_skills.length > 0 && (
              <p style={{ fontSize: 12.5, color: "#5f6878", marginBottom: 0, marginTop: 14 }}>
                Highlighted terms are what the scorer matched against your profile.
              </p>
            )}
          </div>
        </div>

        <div>
          {app.match && (
            <>
              <div className="section-title">Why it scored {Math.round(app.match.score)}</div>
              <div className="card">
                {app.match.reasons.map((reason) => (
                  <div key={reason.component}>
                    <div className="reason">
                      <span className="reason-name">{reason.component}</span>
                      <span className="reason-detail">{reason.detail}</span>
                      <span className="reason-points">{reason.points.toFixed(1)}</span>
                    </div>
                    <div className="bar">
                      <motion.i
                        initial={{ width: 0 }}
                        animate={{ width: `${(reason.points / maxPoints) * 100}%` }}
                        transition={{ duration: 0.7, ease: [0.22, 1, 0.36, 1] }}
                      />
                    </div>
                  </div>
                ))}

                <div className="chips" style={{ marginTop: 14 }}>
                  {app.match.matched_skills.map((s) => (
                    <Chip key={s} tone="on">
                      {s}
                    </Chip>
                  ))}
                  {app.match.missing_skills.map((s) => (
                    <Chip key={s} tone="off">
                      {s}
                    </Chip>
                  ))}
                </div>
              </div>
            </>
          )}

          {app.brief && (
            <>
              <div className="section-title">Company</div>
              <div className="card">
                <p style={{ marginTop: 0, fontSize: 14 }}>{app.brief.summary}</p>
                {Object.entries(app.brief.facts).map(([key, value]) => (
                  <div className="reason" key={key}>
                    <span className="reason-name">{key}</span>
                    <span className="reason-detail">{value}</span>
                    <span />
                  </div>
                ))}
                <p style={{ fontSize: 12.5, color: "#5f6878", marginBottom: 0 }}>
                  Name match confidence: {app.brief.confidence}
                  {app.brief.sources[0] && (
                    <>
                      {" "}
                      &middot;{" "}
                      <a href={app.brief.sources[0]} target="_blank" rel="noreferrer">
                        record
                      </a>
                    </>
                  )}
                </p>
              </div>
            </>
          )}
        </div>
      </div>

      <div className="section-title">Cover letter</div>
      <div className="card">
        {app.letter ? (
          <>
            {!app.letter.edited_by_human && (
              <div className="warn-strip">
                <TriangleAlert size={16} />
                You have not edited this draft. Read it before approving.
              </div>
            )}
            {banned.length > 0 && (
              <div className="warn-strip">
                <TriangleAlert size={16} />
                Contains {banned.length} phrase{banned.length > 1 ? "s" : ""} the drafter
                bans: {banned.join(", ")}
              </div>
            )}

            <textarea
              className="editor"
              value={draft}
              spellCheck
              onChange={(e) => setDraft(e.target.value)}
            />

            <div className="editor-bar">
              <button className="btn sm" onClick={() => void saveLetter()} disabled={!dirty || saving}>
                <Save size={14} />
                {saving ? "Saving" : dirty ? "Save edits" : "Saved"}
              </button>
              <span className="kbd">ctrl+s</span>
              <span style={{ color: "#5f6878", fontSize: 12.5 }}>
                {words} words &middot; drafted by {app.letter.generator}
                {app.letter.evidence_used.length > 0 &&
                  ` from ${app.letter.evidence_used.join(", ")}`}
              </span>
            </div>
          </>
        ) : (
          <p style={{ color: "#5f6878", margin: 0 }}>
            No draft. Either drafting failed or no evidence matched this posting.
          </p>
        )}
      </div>

      <div className="section-title">Next</div>
      <div className="card">
        <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
          {app.stage === "awaiting_review" && (
            <>
              <button className="btn primary" onClick={() => void act("approve")} disabled={busy}>
                <Check size={15} /> Approve
              </button>
              <span className="kbd">a</span>
              <button className="btn danger" onClick={() => void act("skip")} disabled={busy}>
                <SkipForward size={15} /> Skip
              </button>
              <span className="kbd">s</span>
            </>
          )}

          {app.stage === "approved" && (
            <>
              <button className="btn primary" onClick={() => void act("assist")} disabled={busy}>
                <MousePointerClick size={15} /> Open form with my details
              </button>
              <a className="btn" href={app.job.url} target="_blank" rel="noreferrer">
                <ExternalLink size={15} /> Open posting
              </a>
              <button className="btn" onClick={() => void act("markSubmitted")} disabled={busy}>
                <FileText size={15} /> I have submitted it
              </button>
            </>
          )}

          {(app.stage === "submitted" || app.stage === "abandoned" || app.stage === "rejected") && (
            <Link className="btn" to="/">
              <ArrowLeft size={15} /> Back to queue
            </Link>
          )}
        </div>

        <p className="gate-note">
          <ShieldCheck size={13} style={{ verticalAlign: -2, marginRight: 6 }} />
          This tool never presses submit. "Open form with my details" launches a browser,
          fills what it recognises and attaches {meta?.cv_path ?? "your CV"}, then leaves
          the final check and the submit button to you.
        </p>

        {app.notes && (
          <p style={{ fontSize: 12.5, color: "#5f6878", marginBottom: 0 }}>Notes: {app.notes}</p>
        )}
      </div>
    </motion.div>
  );
}
