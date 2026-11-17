import type { ReactNode } from "react";

/** Input style for a page's search box (Plugins, Projects, Library). */
export const pageSearch =
  "min-w-[14rem] flex-1 rounded-lg border border-line bg-panel-2 px-3 py-2 text-[0.88rem] text-text outline-none focus:border-copper";

/** A filter chip / tab on a page's toolbar (All · Documents · …). */
export const pageTab = (on: boolean) =>
  `rounded-lg px-3 py-1.5 text-[0.85rem] ${on ? "bg-panel-2 text-text" : "text-muted hover:text-text"}`;

/** The head of an own page (BACKLOG L24 U5): title, one line of what it is, an optional action on the right
 *  (New project …) and an optional toolbar row under it (search, filters). The body follows as children. */
export default function PageHeader({
  title,
  about,
  action,
  toolbar,
  children,
}: {
  title: string;
  about: string;
  action?: ReactNode;
  toolbar?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="mx-auto w-full max-w-[960px] px-4 py-8">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="m-0 text-[1.6rem] [font-family:var(--font-display)]">{title}</h1>
          <p className="mt-1 text-[0.9rem] text-muted">{about}</p>
        </div>
        {action ? <div className="shrink-0">{action}</div> : null}
      </div>
      {toolbar ? <div className="mt-5 flex flex-wrap items-center gap-3">{toolbar}</div> : null}
      {children}
    </div>
  );
}
