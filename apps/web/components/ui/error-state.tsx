"use client";

import { AlertTriangle, RefreshCw } from "lucide-react";

export function ErrorState({
  title = "暂时无法加载",
  message,
  onRetry,
  compact = false,
}: Readonly<{
  title?: string;
  message: string;
  onRetry?: () => void;
  compact?: boolean;
}>) {
  return (
    <div
      role="alert"
      className={`rounded-2xl border border-coral-100 bg-coral-50 text-ink-800 ${compact ? "p-3" : "p-5"}`}
    >
      <div className="flex items-start gap-3">
        <span className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-xl bg-white text-coral-600">
          <AlertTriangle size={17} />
        </span>
        <div className="min-w-0 flex-1">
          <p className="font-bold">{title}</p>
          <p className="mt-1 text-sm leading-5 text-ink-600">{message}</p>
          {onRetry ? (
            <button
              type="button"
              onClick={onRetry}
              className="btn-secondary mt-3 !min-h-9 !px-3 !py-1.5 text-sm"
            >
              <RefreshCw size={15} />
              重试
            </button>
          ) : null}
        </div>
      </div>
    </div>
  );
}
