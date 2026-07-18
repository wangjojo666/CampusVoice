import { CheckCircle2, RotateCcw } from "lucide-react";

import type { VerifiedFinishEvent } from "@/lib/verified-finish";

export function VerifiedFinish({ event }: Readonly<{ event: VerifiedFinishEvent }>) {
  const undo = event.kind === "undo";

  return (
    <div className="verified-finish rounded-2xl border border-teal-200 bg-white/80 p-4 text-teal-800">
      <div className="flex items-start gap-3">
        <span className="flex size-9 shrink-0 items-center justify-center rounded-xl bg-teal-100">
          {undo ? (
            <RotateCcw size={18} aria-hidden="true" />
          ) : (
            <CheckCircle2 size={18} aria-hidden="true" />
          )}
        </span>
        <div>
          <p className="text-sm font-extrabold">
            {undo ? "撤销结果已经核对好" : "这一步稳稳落地了"}
          </p>
          <p className="mt-1 text-xs leading-5 font-semibold text-teal-700">
            {undo ? "已撤回并通过数据库验证。" : "已通过数据库重新查询核对。"}
          </p>
        </div>
      </div>
    </div>
  );
}
