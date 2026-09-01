"use client";

import { useState } from "react";

import { cn } from "@/lib/utils";

/** Whether this project feeds the shared wiki. The sibling of the per-user
 *  switch in WikiToggle, and deliberately the same control shape, so a developer
 *  reads the two layers as the same kind of decision.
 *
 *  The caller supplies the async work rather than the route, so the page decides
 *  what a change costs and the switch only owns its own pending state — a second
 *  click while a change is in flight is ignored, not queued. */
export default function ProjectWikiToggle({
  shareWiki,
  projectName,
  onToggle,
}: {
  shareWiki: boolean;
  projectName: string;
  onToggle: (shareWiki: boolean) => Promise<void> | void;
}) {
  const [pending, setPending] = useState(false);

  async function toggle() {
    setPending(true);
    try {
      await onToggle(!shareWiki);
    } finally {
      setPending(false);
    }
  }

  return (
    <button
      role="switch"
      aria-checked={shareWiki}
      aria-label={`Feeds shared wiki: ${projectName}`}
      disabled={pending}
      title="Whether this project's sessions may inform the shared wiki"
      onClick={() => void toggle()}
      className="group flex cursor-pointer items-center gap-2 disabled:opacity-50"
    >
      <span
        className={cn(
          "relative h-5 w-9 rounded-full transition-colors",
          shareWiki ? "bg-brand-500" : "bg-border group-hover:bg-muted-foreground/40"
        )}
      >
        <span
          className={cn(
            "absolute top-0.5 h-4 w-4 rounded-full bg-white shadow-sm transition-[left]",
            shareWiki ? "left-[18px]" : "left-0.5"
          )}
        />
      </span>
      <span
        className={cn(
          "text-[12.5px]",
          shareWiki ? "font-medium text-brand-500" : "text-muted-foreground"
        )}
      >
        {shareWiki ? "Feeds shared wiki" : "Not cleared"}
      </span>
    </button>
  );
}
