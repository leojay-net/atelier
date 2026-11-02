export default function Loading() {
  return (
    <div className="space-y-6">
      <div className="h-7 w-20 animate-pulse rounded-sm bg-bg-inset" />
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="h-48 animate-pulse rounded-md bg-bg-inset" />
        <div className="h-48 animate-pulse rounded-md bg-bg-inset" />
      </div>
      <div className="h-64 animate-pulse rounded-md bg-bg-inset" />
    </div>
  );
}
