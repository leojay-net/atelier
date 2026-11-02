import Link from "next/link";
import { ChevronLeft, ChevronRight } from "lucide-react";
import clsx from "clsx";

interface PaginationProps {
  count: number;
  limit: number;
  offset: number;
  /** Builds the href for a given offset, keeping other query params intact. */
  hrefFor: (offset: number) => string;
}

export function Pagination({ count, limit, offset, hrefFor }: PaginationProps) {
  if (count <= limit) return null;
  const page = Math.floor(offset / limit) + 1;
  const pages = Math.ceil(count / limit);
  const prevOffset = Math.max(0, offset - limit);
  const nextOffset = offset + limit;
  const hasPrev = offset > 0;
  const hasNext = nextOffset < count;

  return (
    <nav className="flex items-center justify-between pt-4" aria-label="Pagination">
      <p className="text-[13px] text-fg-secondary">
        Page {page} of {pages}
      </p>
      <div className="flex gap-2">
        <Link
          href={hrefFor(prevOffset)}
          aria-disabled={!hasPrev}
          className={clsx(
            "inline-flex h-8 w-8 items-center justify-center rounded-sm border border-border-strong",
            hasPrev ? "text-fg hover:bg-bg-subtle" : "pointer-events-none text-fg-tertiary opacity-40",
          )}
        >
          <ChevronLeft size={16} />
        </Link>
        <Link
          href={hrefFor(nextOffset)}
          aria-disabled={!hasNext}
          className={clsx(
            "inline-flex h-8 w-8 items-center justify-center rounded-sm border border-border-strong",
            hasNext ? "text-fg hover:bg-bg-subtle" : "pointer-events-none text-fg-tertiary opacity-40",
          )}
        >
          <ChevronRight size={16} />
        </Link>
      </div>
    </nav>
  );
}
