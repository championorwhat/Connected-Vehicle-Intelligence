import { useEffect, useState } from "react";

import { DEMO, demoCapturedAt } from "../api/client";

/** Shown on every page of the GitHub Pages build: what the visitor is looking at, and what it is not. */
export function DemoBanner() {
  const [captured, setCaptured] = useState<string | null>(null);
  useEffect(() => {
    if (DEMO) void demoCapturedAt().then(setCaptured);
  }, []);
  if (!DEMO) return null;
  const when = captured
    ? new Date(captured).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })
    : "…";
  return (
    <p className="demo-banner" role="note">
      <strong>Static demo.</strong> These screens replay what the API returned during a real{" "}
      <code>make pipeline-demo</code> run, recorded on {when}; times are shown as if it were now. Changes you make
      stay in this tab. To run the live system, see{" "}
      <a href="https://github.com/championorwhat/Connected-Vehicle-Intelligence#3-quick-start-macos">the README</a>.
    </p>
  );
}
