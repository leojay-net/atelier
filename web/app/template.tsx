/** A page fades in as you arrive on it (a template remounts on every navigation). */
export default function Template({ children }: { children: React.ReactNode }) {
  return <div className="animate-fade">{children}</div>;
}
