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
      className="w-full max-w-md rounded-xl border border-border bg-bg p-0 shadow-[var(--shadow-dialog)]"
      onClick={(e) => {
        if (e.target === dialogRef.current) dialogRef.current?.close();
      }}
    >
      <div className="flex items-center justify-between border-b border-border px-6 py-4">
        <h2 className="text-[15px] font-semibold tracking-tight text-fg">{title}</h2>
        <button
          type="button"
          aria-label="Close"
          onClick={() => dialogRef.current?.close()}
          className="grid h-7 w-7 place-items-center rounded-full text-fg-tertiary hover:bg-bg-inset hover:text-fg"
        >
          <X size={16} />
        </button>
      </div>
      <div className="px-6 py-5">{children}</div>
    </dialog>
  );
});
