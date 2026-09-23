/** Shared pieces: score ring, chips, toasts, skeletons, empty states. */

import { AnimatePresence, motion } from "framer-motion";
import { AlertTriangle, Check, X } from "lucide-react";
import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";

/* ------------------------------------------------------------------ ring */

/** Score as a ring that fills on mount.
 *
 * Colour is a function of the score rather than fixed crimson: a 55 and an 88
 * should not look equally urgent, and the gradient is the fastest read on the
 * page.
 */
export function ScoreRing({ score, size = 62 }: { score: number; size?: number }) {
  const stroke = size < 50 ? 4 : 5;
  const radius = (size - stroke) / 2;
  const circumference = 2 * Math.PI * radius;
  const clamped = Math.max(0, Math.min(100, score));

  const colour =
    clamped >= 80 ? "#ff2d55" : clamped >= 65 ? "#dc143c" : "#7d5260";

  return (
    <div className="ring" style={{ width: size, height: size }}>
      <svg width={size} height={size}>
        <circle
          className="ring-track"
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          strokeWidth={stroke}
        />
        <motion.circle
          className="ring-value"
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke={colour}
          strokeWidth={stroke}
          strokeDasharray={circumference}
          initial={{ strokeDashoffset: circumference }}
          animate={{ strokeDashoffset: circumference * (1 - clamped / 100) }}
          transition={{ duration: 0.9, ease: [0.22, 1, 0.36, 1] }}
        />
      </svg>
      <span
        className="ring-label"
        style={{ fontSize: size < 50 ? 14 : 18, color: colour }}
      >
        {Math.round(clamped)}
      </span>
    </div>
  );
}

/* ----------------------------------------------------------------- misc */

export function Chip({
  children,
  tone = "",
}: {
  children: ReactNode;
  tone?: "" | "on" | "off" | "warn";
}) {
  return <span className={`chip ${tone}`}>{children}</span>;
}

export function Skeletons({ count = 4 }: { count?: number }) {
  return (
    <>
      {Array.from({ length: count }, (_, i) => (
        <div key={i} className="skeleton" style={{ animationDelay: `${i * 0.08}s` }} />
      ))}
    </>
  );
}

export function Empty({
  icon,
  title,
  children,
}: {
  icon: ReactNode;
  title: string;
  children?: ReactNode;
}) {
  return (
    <motion.div
      className="empty"
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
    >
      {icon}
      <h3>{title}</h3>
      <div>{children}</div>
    </motion.div>
  );
}

/* --------------------------------------------------------------- toasts */

type Toast = { id: number; message: string; tone: "ok" | "err" };

const ToastContext = createContext<(message: string, tone?: "ok" | "err") => void>(
  () => {},
);

export const useToast = () => useContext(ToastContext);

export function ToastHost({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);

  const push = useCallback((message: string, tone: "ok" | "err" = "ok") => {
    const id = Date.now() + Math.random();
    setToasts((current) => [...current, { id, message, tone }]);
    // Errors linger: a 409 from the human gate is worth reading twice.
    setTimeout(() => {
      setToasts((current) => current.filter((t) => t.id !== id));
    }, tone === "err" ? 6000 : 3000);
  }, []);

  const value = useMemo(() => push, [push]);

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className="toasts">
        <AnimatePresence>
          {toasts.map((toast) => (
            <motion.div
              key={toast.id}
              className={`toast ${toast.tone}`}
              initial={{ opacity: 0, x: 40, scale: 0.96 }}
              animate={{ opacity: 1, x: 0, scale: 1 }}
              exit={{ opacity: 0, x: 40, scale: 0.96 }}
              transition={{ type: "spring", stiffness: 420, damping: 32 }}
            >
              {toast.tone === "ok" ? (
                <Check size={16} color="#3ddc97" />
              ) : (
                <AlertTriangle size={16} color="#ff4d4d" />
              )}
              <span>{toast.message}</span>
            </motion.div>
          ))}
        </AnimatePresence>
      </div>
    </ToastContext.Provider>
  );
}

export function CloseButton({ onClick }: { onClick: () => void }) {
  return (
    <button className="btn sm ghost" onClick={onClick} aria-label="dismiss">
      <X size={14} />
    </button>
  );
}

/** Highlight the skills the scorer matched, inside the job description.
 *
 * Shows you what it saw, rather than asking you to trust the number. Split on
 * a single regex so overlapping terms cannot double-wrap.
 */
export function HighlightedText({
  text,
  terms,
}: {
  text: string;
  terms: string[];
}) {
  const parts = useMemo(() => {
    if (!terms.length) return [text];
    const escaped = terms
      .filter(Boolean)
      .sort((a, b) => b.length - a.length)
      .map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
    const pattern = new RegExp(`(${escaped.join("|")})`, "gi");
    return text.split(pattern);
  }, [text, terms]);

  const lowered = useMemo(() => new Set(terms.map((t) => t.toLowerCase())), [terms]);

  return (
    <>
      {parts.map((part, index) =>
        lowered.has(part.toLowerCase()) ? (
          <mark key={index}>{part}</mark>
        ) : (
          <span key={index}>{part}</span>
        ),
      )}
    </>
  );
}
