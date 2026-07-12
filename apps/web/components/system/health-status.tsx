"use client";

import type { HealthResponse } from "@campusvoice/shared-types";
import { useCallback, useEffect, useState } from "react";

import { ApiError, api } from "@/lib/api-client";

export function HealthStatus({ compact = false }: Readonly<{ compact?: boolean }>) {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const check = useCallback(async () => {
    setLoading(true);
    try {
      setHealth(await api.health());
      setError(null);
    } catch (reason) {
      setHealth(null);
      setError(reason instanceof ApiError ? reason.userMessage : "后端服务未连接");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => void check(), 0);
    return () => window.clearTimeout(timer);
  }, [check]);

  const healthy = health?.status === "ok";
  const label = loading ? "正在检查服务" : healthy ? "服务已连接" : (error ?? "服务异常");

  return (
    <button
      type="button"
      onClick={() => void check()}
      title={label}
      aria-label={label}
      className={`flex items-center rounded-xl border border-mist-200 bg-white/80 text-left ${compact ? "gap-2 px-2.5 py-2" : "gap-3 px-3.5 py-3"}`}
    >
      <span
        className={`size-2.5 shrink-0 rounded-full ${loading ? "animate-pulse bg-gold-500" : healthy ? "bg-teal-500" : "bg-coral-500"}`}
        aria-hidden="true"
      />
      <span className={`${compact ? "text-xs" : "text-sm"} truncate font-semibold text-ink-600`}>
        {label}
      </span>
    </button>
  );
}
