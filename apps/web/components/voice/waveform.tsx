export function Waveform({ level, active }: Readonly<{ level: number; active: boolean }>) {
  return (
    <div
      className="flex h-16 items-center justify-center gap-1"
      aria-label={active ? "正在显示实时音量" : "当前没有音频输入"}
    >
      {Array.from({ length: 31 }, (_, index) => {
        const shape = 0.32 + Math.abs(Math.sin((index + 2) * 0.72)) * 0.68;
        const live = active ? Math.max(0.08, level) : 0.04;
        const height = 7 + live * shape * 49;
        return (
          <span
            key={index}
            className={`wave-bar w-1 rounded-full ${active ? "bg-teal-500" : "bg-mist-200"}`}
            style={{ height: `${height}px` }}
          />
        );
      })}
    </div>
  );
}
