/** The Atelier mark: a rounded tile holding a frame with a play wedge cut through it. Flat,
 * one accent colour, drawn so it stays crisp at any size. */
export function Logo({ size = 28 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 48 48" role="img" aria-label="Atelier" className="shrink-0">
      <rect width="48" height="48" rx="12" fill="var(--color-accent)" />
      <rect x="11" y="13" width="26" height="22" rx="5" fill="none" stroke="#fff" strokeWidth="2.6" opacity="0.95" />
      <path d="M21 19.2v9.6a1 1 0 0 0 1.5.86l7.8-4.8a1 1 0 0 0 0-1.72l-7.8-4.8a1 1 0 0 0-1.5.86Z" fill="#fff" />
    </svg>
  );
}
