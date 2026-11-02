"use client";

import { useEffect, useImperativeHandle, useRef, forwardRef } from "react";
import type { ReactNode } from "react";
import { X } from "lucide-react";

export interface DialogHandle {
  show: () => void;
  close: () => void;
}

interface DialogProps {
  title: string;
  children: ReactNode;
  onClose?: () => void;
}

export const Dialog = forwardRef<DialogHandle, DialogProps>(function Dialog(
  { title, children, onClose },
  ref,
) {
  const dialogRef = useRef<HTMLDialogElement>(null);

  useImperativeHandle(ref, () => ({
    show: () => dialogRef.current?.showModal(),
    close: () => dialogRef.current?.close(),
  }));

  useEffect(() => {
    const el = dialogRef.current;
    if (!el) return;
    const handleClose = () => onClose?.();
    el.addEventListener("close", handleClose);
    return () => el.removeEventListener("close", handleClose);
  }, [onClose]);

  return (
    <dialog
      ref={dialogRef}
      className="w-full max-w-md rounded-lg border border-border bg-bg p-0 shadow-[var(--shadow-dialog)] backdrop:bg-black/35"
      onClick={(e) => {
        if (e.target === dialogRef.current) dialogRef.current?.close();
      }}
    >
      <div className="flex items-center justify-between border-b border-border px-5 py-4">
        <h2 className="text-sm font-semibold text-fg">{title}</h2>
        <button
          type="button"
          aria-label="Close"
          onClick={() => dialogRef.current?.close()}
          className="rounded-sm p-1 text-fg-tertiary hover:bg-bg-subtle hover:text-fg"
        >
          <X size={16} />
        </button>
      </div>
      <div className="px-5 py-4">{children}</div>
    </dialog>
  );
});
