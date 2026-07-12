import { Inbox } from "lucide-react";

export function EmptyState({
  title,
  description,
  action,
}: Readonly<{ title: string; description: string; action?: React.ReactNode }>) {
  return (
    <div className="flex min-h-48 flex-col items-center justify-center rounded-2xl border border-dashed border-mist-200 bg-mist-50/50 px-5 py-8 text-center">
      <span className="mb-3 flex size-11 items-center justify-center rounded-2xl bg-white text-ink-400 shadow-sm">
        <Inbox size={21} />
      </span>
      <p className="font-bold text-ink-800">{title}</p>
      <p className="mt-1 max-w-md text-sm leading-5 text-ink-500">{description}</p>
      {action ? <div className="mt-4">{action}</div> : null}
    </div>
  );
}
