export default function Loading() {
  return (
    <div className="space-y-6">
      <div className="h-7 w-24 animate-pulse rounded-sm bg-bg-inset" />
      <div className="flex gap-1.5">
        {Array.from({ length: 5 }).map((_, i) => (
          <div key={i} className="h-7 w-16 animate-pulse rounded-sm bg-bg-inset" />
        ))}
      </div>
      <div className="space-y-1.5">
        {Array.from({ length: 6 }).map((_, i) => (
          <div key={i} className="h-12 animate-pulse rounded-sm bg-bg-inset" />
        ))}
      </div>
    </div>
  );
}
