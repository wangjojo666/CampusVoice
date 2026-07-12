"use client";

import { HelpCircle, Send } from "lucide-react";
import { useState } from "react";

export function ClarificationCard({
  question,
  missingFields = [],
  candidates = [],
  onSubmit,
  onSelectCandidate,
  busy = false,
}: Readonly<{
  question: string;
  missingFields?: string[];
  candidates?: Array<{ id: string; label: string }>;
  onSubmit: (answer: string) => void | Promise<void>;
  onSelectCandidate?: (id: string) => void | Promise<void>;
  busy?: boolean;
}>) {
  const [answer, setAnswer] = useState("");
  return (
    <section className="surface border !border-gold-100 p-5" aria-labelledby="clarification-title">
      <div className="flex items-start gap-3">
        <span className="flex size-10 shrink-0 items-center justify-center rounded-2xl bg-gold-100 text-amber-700">
          <HelpCircle size={20} />
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-xs font-bold tracking-wider text-amber-700 uppercase">
            还需要一项信息
          </p>
          <h2 id="clarification-title" className="mt-1 text-lg font-extrabold text-ink-950">
            {question}
          </h2>
          {missingFields.length > 0 ? (
            <p className="mt-1 text-xs text-ink-400">缺少字段：{missingFields.join("、")}</p>
          ) : null}
          {candidates.length > 0 ? (
            <div className="mt-4 grid gap-2" aria-label="可选目标">
              {candidates.map((candidate) => (
                <button
                  key={candidate.id}
                  type="button"
                  disabled={busy}
                  onClick={() => void onSelectCandidate?.(candidate.id)}
                  className="rounded-xl border border-mist-200 bg-white px-3 py-2 text-left text-sm font-semibold text-ink-700 hover:border-teal-300 hover:bg-teal-50"
                >
                  {candidate.label}
                </button>
              ))}
            </div>
          ) : null}
          <form
            className="mt-4 flex flex-col gap-2 sm:flex-row"
            onSubmit={(event) => {
              event.preventDefault();
              if (answer.trim()) void onSubmit(answer.trim());
            }}
          >
            <input
              value={answer}
              onChange={(event) => setAnswer(event.target.value)}
              className="field flex-1"
              placeholder="输入补充信息"
              aria-label="补充信息"
            />
            <button
              type="submit"
              disabled={busy || !answer.trim()}
              className="btn-primary shrink-0"
            >
              <Send size={16} />
              {busy ? "处理中" : "补充并继续"}
            </button>
          </form>
        </div>
      </div>
    </section>
  );
}
