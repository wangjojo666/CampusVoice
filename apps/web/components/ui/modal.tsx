"use client";

import { X } from "lucide-react";
import { useId, useRef } from "react";
import { createPortal } from "react-dom";

import { useModalDialog } from "@/hooks/use-modal-dialog";

export function Modal({
  open,
  title,
  description,
  onClose,
  children,
  wide = false,
}: Readonly<{
  open: boolean;
  title: string;
  description?: string;
  onClose: () => void;
  children: React.ReactNode;
  wide?: boolean;
}>) {
  const titleId = useId();
  const descriptionId = useId();
  const backdropRef = useRef<HTMLDivElement>(null);
  const dialogRef = useRef<HTMLElement>(null);

  useModalDialog({ open, onClose, backdropRef, dialogRef });

  if (!open || typeof document === "undefined") return null;

  return createPortal(
    <div
      ref={backdropRef}
      className="fixed inset-0 z-50 flex items-end justify-center bg-ink-950/35 p-0 backdrop-blur-sm sm:items-center sm:p-5"
      onMouseDown={onClose}
    >
      <section
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={description ? descriptionId : undefined}
        tabIndex={-1}
        className={`max-h-[92vh] w-full overflow-y-auto rounded-t-3xl bg-white p-5 shadow-2xl sm:rounded-3xl sm:p-6 ${wide ? "max-w-3xl" : "max-w-xl"}`}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="mb-5 flex items-start justify-between gap-4">
          <div>
            <h2 id={titleId} className="text-xl font-extrabold tracking-tight text-ink-950">
              {title}
            </h2>
            {description ? (
              <p id={descriptionId} className="mt-1 text-sm leading-5 text-ink-500">
                {description}
              </p>
            ) : null}
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="关闭"
            className="btn-ghost !size-9 !min-h-0 !p-0"
          >
            <X size={19} />
          </button>
        </header>
        {children}
      </section>
    </div>,
    document.body,
  );
}
