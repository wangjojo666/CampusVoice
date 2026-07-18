"use client";

import {
  CalendarDays,
  CircleUserRound,
  FileText,
  Home,
  ListTodo,
  LogOut,
  Mic2,
  Waves,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState, useSyncExternalStore } from "react";

import { ErrorState } from "@/components/ui/error-state";
import { LoadingState } from "@/components/ui/loading-state";
import { HealthStatus } from "@/components/system/health-status";
import { api, API_BASE_URL } from "@/lib/api-client";
import { OIDC_ENABLED } from "@/lib/auth";
import { setCurrentUserSettings } from "@/lib/user-settings";

const navigation = [
  { href: "/", label: "今天", icon: Home },
  { href: "/voice", label: "问声程", icon: Mic2 },
  { href: "/tasks", label: "计划", icon: ListTodo },
  { href: "/calendar", label: "日程", icon: CalendarDays },
  { href: "/notices", label: "校园情报", icon: FileText },
] as const;

function isActive(pathname: string, href: string) {
  if (href === "/notices" && pathname.startsWith("/radar")) return true;
  return href === "/" ? pathname === "/" : pathname.startsWith(href);
}

function subscribeToLocation() {
  return () => undefined;
}

function currentAuthError() {
  return new URL(window.location.href).searchParams.get("auth_error");
}

export function AppShell({
  children,
  oidcEnabled = OIDC_ENABLED,
}: Readonly<{ children: React.ReactNode; oidcEnabled?: boolean }>) {
  const pathname = usePathname();
  const authError = useSyncExternalStore(subscribeToLocation, currentAuthError, () => null);
  const [loggingOut, setLoggingOut] = useState(false);
  const [logoutError, setLogoutError] = useState(false);
  const [settingsState, setSettingsState] = useState<"loading" | "ready" | "error">("loading");
  const settingsRequest = useRef(0);

  const retrySettings = () => {
    const request = ++settingsRequest.current;
    setSettingsState("loading");
    void api.settings.get().then(
      (settings) => {
        if (request !== settingsRequest.current) return;
        setCurrentUserSettings(settings);
        setSettingsState("ready");
      },
      () => {
        if (request === settingsRequest.current) setSettingsState("error");
      },
    );
  };

  useEffect(() => {
    const request = ++settingsRequest.current;
    void api.settings.get().then(
      (settings) => {
        if (request !== settingsRequest.current) return;
        setCurrentUserSettings(settings);
        setSettingsState("ready");
      },
      () => {
        if (request === settingsRequest.current) setSettingsState("error");
      },
    );
    return () => {
      settingsRequest.current += 1;
    };
  }, []);

  async function logout() {
    if (loggingOut) return;
    setLoggingOut(true);
    setLogoutError(false);
    try {
      const result = await api.auth.logout();
      window.location.assign(result.logout_url);
    } catch {
      setLogoutError(true);
    } finally {
      setLoggingOut(false);
    }
  }

  return (
    <div className="min-h-screen lg:grid lg:grid-cols-[264px_minmax(0,1fr)]">
      <a
        href="#main-content"
        className="sr-only fixed top-3 left-3 z-50 rounded-xl bg-ink-950 px-4 py-3 font-bold text-white focus:fixed focus:not-sr-only"
      >
        跳到主要内容
      </a>
      <aside className="sticky top-0 hidden h-screen border-r border-mist-200/80 bg-white/72 px-5 py-6 backdrop-blur-xl lg:flex lg:flex-col">
        <Link href="/" className="mb-9 flex items-center gap-3 rounded-xl px-2 py-1">
          <span className="flex size-11 items-center justify-center rounded-2xl bg-teal-600 text-white shadow-[0_8px_24px_rgba(14,127,109,.2)]">
            <Waves size={23} strokeWidth={2.3} />
          </span>
          <span>
            <span className="block text-[1.12rem] font-extrabold tracking-tight text-ink-950">
              声程
            </span>
            <span className="block text-[0.68rem] font-semibold tracking-[0.14em] text-ink-500 uppercase">
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
              <p className="truncate text-sm font-bold text-ink-800">
                {oidcEnabled ? "校园账户" : "本地演示用户"}
              </p>
              <p className="truncate text-xs text-ink-500">
                {oidcEnabled ? "校园统一身份" : "单用户 · 数据仅存本机"}
              </p>
            </div>
          </div>
          {oidcEnabled ? (
            <button
              type="button"
              onClick={() => void logout()}
              disabled={loggingOut}
              className="flex w-full items-center justify-center gap-2 rounded-xl border border-mist-200 px-3 py-2 text-sm font-semibold text-ink-600 hover:bg-mist-50"
            >
              <LogOut size={16} />
              {loggingOut ? "正在退出" : "退出登录"}
            </button>
          ) : null}
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
          <div className="flex items-center gap-2">
            <HealthStatus compact />
            {oidcEnabled ? (
              <button
                type="button"
                aria-label="退出登录"
                title="退出登录"
                onClick={() => void logout()}
                disabled={loggingOut}
                className="flex size-9 items-center justify-center rounded-xl border border-mist-200 text-ink-500"
              >
                <LogOut size={17} />
              </button>
            ) : null}
          </div>
        </header>
        <main
          id="main-content"
          tabIndex={-1}
          className="mx-auto w-full max-w-[1480px] px-4 py-6 pb-28 outline-none sm:px-6 lg:px-9 lg:py-9 lg:pb-12"
        >
          {authError ? (
            <div
              role="alert"
              className="mb-5 rounded-xl border border-red-200 bg-red-50 p-3 text-sm text-red-800"
            >
              校园登录未完成（{authError}）。若问题持续，请联系管理员。
              <a className="ml-2 font-bold underline" href={`${API_BASE_URL}/api/auth/login`}>
                重新登录
              </a>
            </div>
          ) : null}
          {logoutError ? (
            <div
              role="alert"
              className="mb-5 rounded-xl border border-red-200 bg-red-50 p-3 text-sm text-red-800"
            >
              退出登录未完成，当前会话仍然有效。请检查网络后重试。
              <button
                type="button"
                className="ml-2 font-bold underline"
                onClick={() => void logout()}
              >
                重试退出
              </button>
            </div>
          ) : null}
          {settingsState === "ready" ? children : null}
          {settingsState === "loading" ? <LoadingState rows={4} label="正在加载个人设置" /> : null}
          {settingsState === "error" ? (
            <ErrorState
              title="无法加载个人设置"
              message="为避免使用错误的时区或默认提醒，数据写入入口暂不可用。"
              onRetry={retrySettings}
            />
          ) : null}
        </main>
      </div>

      <nav
        aria-label="移动端主导航"
        className="fixed inset-x-3 bottom-3 z-40 grid grid-cols-5 rounded-2xl border border-mist-200 bg-white/94 p-1.5 shadow-[0_16px_45px_rgba(18,27,34,.18)] backdrop-blur-xl lg:hidden"
      >
        {navigation.map(({ href, label, icon: Icon }) => {
          const active = isActive(pathname, href);
          return (
            <Link
              key={href}
              href={href}
              aria-current={active ? "page" : undefined}
              className={`flex min-h-11 min-w-0 flex-col items-center justify-center gap-1 rounded-xl px-1 py-2 text-[0.7rem] font-bold ${
                active ? "bg-teal-50 text-teal-700" : "text-ink-600"
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
