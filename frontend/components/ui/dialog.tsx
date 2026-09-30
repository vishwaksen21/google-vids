"use client";

import { X } from "lucide-react";
import { useEffect, useId, useRef, type ReactNode } from "react";

/**
 * Modal dialog on the native <dialog> element: focus is moved in and trapped by the
 * browser, Escape closes it, and focus returns to the opener on close.
 */
export function Dialog({
  open,
  onClose,
  title,
  description,
  children,
  footer,
  dismissible = true,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  description?: ReactNode;
  children?: ReactNode;
  footer?: ReactNode;
  dismissible?: boolean;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const descId = useId();

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (open && !el.open) el.showModal();
    if (!open && el.open) el.close();
  }, [open]);

  return (
    <dialog
      ref={ref}
      aria-labelledby={titleId}
      aria-describedby={description ? descId : undefined}
      onCancel={(e) => {
        e.preventDefault();
        if (dismissible) onClose();
      }}
      onClose={() => {
        if (open) onClose();
      }}
      className="m-auto w-[min(560px,calc(100vw-32px))] rounded-2xl border border-gray-200 bg-white p-0 text-ink shadow-dialog"
    >
      {open ? (
        <div className="flex max-h-[85vh] flex-col">
          <div className="flex items-start gap-4 border-b border-gray-100 px-6 pt-5 pb-4">
            <div className="min-w-0 flex-1">
              <h2 id={titleId} className="text-[18px] font-semibold leading-snug">
                {title}
              </h2>
              {description ? (
                <div id={descId} className="mt-1 text-[14px] text-gray">
                  {description}
                </div>
              ) : null}
            </div>
            {dismissible ? (
              <button type="button" onClick={onClose} className="-mr-2 rounded-md p-1.5 text-gray hover:bg-gray-50 hover:text-ink" aria-label="Close">
                <X aria-hidden className="size-4" />
              </button>
            ) : null}
          </div>
          {children ? <div className="overflow-y-auto px-6 py-5">{children}</div> : null}
          {footer ? <div className="flex flex-wrap justify-end gap-2 border-t border-gray-100 px-6 py-4">{footer}</div> : null}
        </div>
      ) : null}
    </dialog>
  );
}
