export function LoadingState({
  rows = 3,
  label = "正在加载",
}: Readonly<{ rows?: number; label?: string }>) {
  return (
    <div aria-busy="true" aria-label={label} className="space-y-3">
      {Array.from({ length: rows }, (_, index) => (
        <div key={index} className="h-20 animate-pulse rounded-2xl bg-mist-100" />
      ))}
    </div>
  );
}
