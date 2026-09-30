import type { ReactNode } from "react";

/** One line saying what the page is for, and an optional "How to use this page" that opens on click. */
export function PageIntro({ lead, children }: { lead: ReactNode; children?: ReactNode }) {
  return (
    <div className="intro">
      <p className="lead">{lead}</p>
      {children && (
        <details className="howto">
          <summary>How to use this page</summary>
          {children}
        </details>
      )}
    </div>
  );
}

/** Severity in words and colour, so it is never colour alone. */
export function SeverityBadge({ severity }: { severity: string }) {
  const tone = severity === "critical" ? "high" : severity === "warning" ? "medium" : "low";
  const word = severity === "critical" ? "Critical" : severity === "warning" ? "Warning" : "Info";
  return (
    <span className={`badge ${tone}`}>
      <span aria-hidden="true">{severity === "critical" ? "● " : severity === "warning" ? "▲ " : "○ "}</span>
      {word}
    </span>
  );
}
