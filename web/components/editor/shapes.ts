/** Drawn stickers: flat shapes on a 24-unit grid, filled with one colour. They are painted on a
 * canvas and added like any picture, so they glide, grow, spin and fade like the rest. */
export const STICKER_SHAPES: { key: string; label: string; path: string; evenodd?: boolean }[] = [
  { key: "star", label: "Star", path: "M12 2.5l2.9 6.1 6.6.9-4.8 4.6 1.2 6.6L12 17.6 6.1 20.7l1.2-6.6L2.5 9.5l6.6-.9z" },
  { key: "heart", label: "Heart", path: "M12 21s-7.5-4.6-9.6-9.2C.9 8.3 3 4.5 6.6 4.5c2 0 3.6 1 5.4 3 1.8-2 3.4-3 5.4-3 3.6 0 5.7 3.8 4.2 7.3C19.5 16.4 12 21 12 21z" },
  { key: "sparkle", label: "Sparkle", path: "M12 2c.8 5.5 4.5 9.2 10 10-5.5.8-9.2 4.5-10 10-.8-5.5-4.5-9.2-10-10 5.5-.8 9.2-4.5 10-10z" },
  { key: "bolt", label: "Bolt", path: "M13.5 2L4.5 13.5h6L9.5 22l9-11.5h-6z" },
  { key: "arrow", label: "Arrow", path: "M3 10h11V5l8 7-8 7v-5H3z" },
  { key: "circle", label: "Circle", path: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18z" },
  { key: "ring", label: "Ring", path: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zm0 4a5 5 0 1 1 0 10 5 5 0 0 1 0-10z", evenodd: true },
  { key: "diamond", label: "Diamond", path: "M12 2l9 10-9 10L3 12z" },
  { key: "bubble", label: "Bubble", path: "M5 4h14a3 3 0 0 1 3 3v8a3 3 0 0 1-3 3h-6.5L8 22v-4H5a3 3 0 0 1-3-3V7a3 3 0 0 1 3-3z" },
  { key: "check", label: "Check", path: "M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm-1.6 14.4-4-4 1.5-1.5 2.5 2.5 5.7-5.7 1.5 1.5z", evenodd: true },
  { key: "plus", label: "Plus", path: "M10 3h4v7h7v4h-7v7h-4v-7H3v-4h7z" },
  { key: "play", label: "Play", path: "M7 4.5v15l13-7.5z" },
  { key: "pin", label: "Pin", path: "M12 2a7 7 0 0 0-7 7c0 5.2 7 13 7 13s7-7.8 7-13a7 7 0 0 0-7-7zm0 9.5a2.5 2.5 0 1 1 0-5 2.5 2.5 0 0 1 0 5z", evenodd: true },
  { key: "crown", label: "Crown", path: "M3 18l-1-11 5.5 4L12 4l4.5 7L22 7l-1 11z" },
];

export const SHAPE_COLORS = ["#ffffff", "#ffd60a", "#ff453a", "#30d158", "#0a84ff", "#bf5af2", "#1c1c1e"];

/** Paint a shape as a transparent PNG, ready to upload as a sticker. */
export function drawShape(key: string, color: string): Promise<Blob | null> {
  const shape = STICKER_SHAPES.find((s) => s.key === key);
  const c = document.createElement("canvas");
  c.width = c.height = 512;
  const ctx = c.getContext("2d");
  if (!shape || !ctx) return Promise.resolve(null);
  ctx.scale(512 / 24, 512 / 24);
  ctx.fillStyle = color;
  ctx.fill(new Path2D(shape.path), shape.evenodd ? "evenodd" : "nonzero");
  return new Promise((ok) => c.toBlob(ok, "image/png"));
}
