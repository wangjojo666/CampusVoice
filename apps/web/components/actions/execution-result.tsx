import type { VerificationResult } from "@campusvoice/shared-types";
import { AlertCircle, CheckCircle2, Database, RotateCcw, XCircle } from "lucide-react";

export function ExecutionResult({
  result,
  onRetry,
  onUndo,
}: Readonly<{
  result: VerificationResult;
  onRetry?: () => void;
  onUndo?: () => void;
}>) {
  return (
    <section
      aria-live="polite"
      className={`surface overflow-hidden border ${result.success ? "!border-teal-100" : "!border-coral-100"}`}
    >
      <div
        className={`flex items-start gap-3 p-5 ${result.success ? "bg-teal-50/70" : "bg-coral-50/80"}`}
      >
        {result.success ? (
          <CheckCircle2 className="mt-0.5 shrink-0 text-teal-600" size={24} />
        ) : (
          <XCircle className="mt-0.5 shrink-0 text-coral-600" size={24} />
        )}
        <div>
          <p className={`font-extrabold ${result.success ? "text-teal-700" : "text-coral-600"}`}>
            {result.success ? "数据库验证成功" : "操作未能验证成功"}
          </p>
          <p className="mt-1 text-sm leading-6 text-ink-700">{result.message}</p>
        </div>
      </div>

      <div className="p-5">
        {result.record_id ? (
          <p className="mb-4 flex items-center gap-2 text-sm text-ink-500">
            <Database size={16} /> 记录 ID：
            <code className="rounded bg-mist-100 px-1.5 py-0.5 text-xs">{result.record_id}</code>
          </p>
        ) : null}
        {Object.keys(result.verified_fields).length > 0 ? (
          <div>
            <p className="mb-2 text-xs font-bold text-ink-400">重新查询后的字段核验</p>
            <div className="flex flex-wrap gap-2">
              {Object.entries(result.verified_fields).map(([field, verified]) => (
                <span
                  key={field}
                  className={`inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-xs font-bold ${verified ? "bg-teal-50 text-teal-700" : "bg-coral-50 text-coral-600"}`}
                >
                  {verified ? <CheckCircle2 size={13} /> : <AlertCircle size={13} />}
                  {field}
                </span>
              ))}
            </div>
          </div>
        ) : null}
        {result.side_effects.length > 0 ? (
          <div className="mt-4 rounded-xl bg-gold-100/45 p-3 text-sm text-amber-800">
            <p className="font-bold">检测到附带影响</p>
            <ul className="mt-1 list-inside list-disc space-y-1">
              {result.side_effects.map((effect) => (
                <li key={effect}>{effect}</li>
              ))}
            </ul>
          </div>
        ) : null}
        {!result.success && result.failure_reason ? (
          <p className="mt-4 text-sm text-coral-600">原因：{result.failure_reason}</p>
        ) : null}
        <div className="mt-5 flex flex-wrap gap-2">
          {!result.success && result.retryable && onRetry ? (
            <button type="button" onClick={onRetry} className="btn-primary">
              <RotateCcw size={16} /> 重试一次
            </button>
          ) : null}
          {result.success && onUndo ? (
            <button type="button" onClick={onUndo} className="btn-secondary">
              <RotateCcw size={16} /> 撤销本次操作
            </button>
          ) : null}
        </div>
      </div>
    </section>
  );
}
