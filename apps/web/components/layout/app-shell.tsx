"use client";

import {
  CalendarDays,
  CircleUserRound,
  FileText,
  Home,
  ListTodo,
  Mic2,
  Settings2,
  Waves,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { HealthStatus } from "@/components/system/health-status";

const navigation = [
  { href: "/", label: "首页", icon: Home },
  { href: "/voice", label: "语音助手", icon: Mic2 },
  { href: "/tasks", label: "待办", icon: ListTodo },
  { href: "/calendar", label: "日历", icon: CalendarDays },
  { href: "/notices", label: "校园通知", icon: FileText },
  { href: "/settings", label: "热词与设置", icon: Settings2 },
] as const;

function isActive(pathname: string, href: string) {
  return href === "/" ? pathname === "/" : pathname.startsWith(href);
}

export function AppShell({ children }: Readonly<{ children: React.ReactNode }>) {
  const pathname = usePathname();

  return (
    <div className="min-h-screen lg:grid lg:grid-cols-[264px_minmax(0,1fr)]">
      <aside className="sticky top-0 hidden h-screen border-r border-mist-200/80 bg-white/72 px-5 py-6 backdrop-blur-xl lg:flex lg:flex-col">
        <Link href="/" className="mb-9 flex items-center gap-3 rounded-xl px-2 py-1">
          <span className="flex size-11 items-center justify-center rounded-2xl bg-teal-600 text-white shadow-[0_8px_24px_rgba(14,127,109,.2)]">
            <Waves size={23} strokeWidth={2.3} />
          </span>
          <span>
            <span className="block text-[1.12rem] font-extrabold tracking-tight text-ink-950">
              声程
            </span>
            <span className="block text-[0.68rem] font-semibold tracking-[0.14em] text-ink-400 uppercase">
              CampusVoice
            </span>
          </span>
        </Link>

        <nav aria-label="主导航" className="space-y-1.5">
          {navigation.map(({ href, label, icon: Icon }) => {
            const active = isActive(pathname, href);
            return (
              <Link
                key={href}
                href={href}
                aria-current={active ? "page" : undefined}
                className={`flex items-center gap-3 rounded-xl px-3.5 py-3 text-sm font-semibold transition-colors ${
                  active
                    ? "bg-teal-50 text-teal-700"
                    : "text-ink-600 hover:bg-mist-100 hover:text-ink-950"
                }`}
              >
                <Icon size={19} strokeWidth={active ? 2.4 : 1.9} />
                {label}
              </Link>
            );
          })}
        </nav>

        <div className="mt-auto space-y-3">
          <HealthStatus compact />
          <div className="flex items-center gap-3 rounded-2xl border border-mist-200 bg-mist-50 p-3">
            <CircleUserRound className="text-teal-600" size={28} />
            <div className="min-w-0">
              <p className="truncate text-sm font-bold text-ink-800">本地演示用户</p>
              <p className="truncate text-xs text-ink-400">单用户 · 数据仅存本机</p>
            </div>
          </div>
        </div>
      </aside>

      <div className="min-w-0">
        <header className="sticky top-0 z-30 flex h-16 items-center justify-between border-b border-mist-200/80 bg-white/80 px-4 backdrop-blur-xl lg:hidden">
          <Link href="/" className="flex items-center gap-2.5">
            <span className="flex size-9 items-center justify-center rounded-xl bg-teal-600 text-white">
              <Waves size={20} />
            </span>
            <span className="font-extrabold tracking-tight">声程</span>
          </Link>
          <HealthStatus compact />
        </header>
        <main className="mx-auto w-full max-w-[1480px] px-4 py-6 pb-28 sm:px-6 lg:px-9 lg:py-9 lg:pb-12">
          {children}
        </main>
      </div>

      <nav
        aria-label="移动端主导航"
        className="fixed inset-x-3 bottom-3 z-40 grid grid-cols-6 rounded-2xl border border-mist-200 bg-white/94 p-1.5 shadow-[0_16px_45px_rgba(18,27,34,.18)] backdrop-blur-xl lg:hidden"
      >
        {navigation.map(({ href, label, icon: Icon }) => {
          const active = isActive(pathname, href);
          return (
            <Link
              key={href}
              href={href}
              aria-current={active ? "page" : undefined}
              className={`flex min-w-0 flex-col items-center gap-1 rounded-xl px-1 py-2 text-[0.62rem] font-bold ${
                active ? "bg-teal-50 text-teal-700" : "text-ink-400"
              }`}
            >
              <Icon size={18} />
              <span className="max-w-full truncate">{label}</span>
            </Link>
          );
        })}
      </nav>
    </div>
  );
}
