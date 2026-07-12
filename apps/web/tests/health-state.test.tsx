import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { HealthStatus } from "@/components/system/health-status";

describe("HealthStatus", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });

  it("shows the verified healthy state returned by the API", async () => {
    vi.mocked(fetch).mockResolvedValue(
      new Response(JSON.stringify({ status: "ok", service: "CampusVoice API", version: "0.1.0" }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
    render(<HealthStatus />);
    expect(screen.getByRole("button", { name: "正在检查服务" })).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "服务已连接" })).toBeInTheDocument();
  });

  it("shows a disconnected state instead of claiming success", async () => {
    vi.mocked(fetch).mockRejectedValue(new TypeError("network error"));
    render(<HealthStatus />);
    expect(
      await screen.findByRole("button", { name: "无法连接服务，请确认后端已启动并检查网络。" }),
    ).toBeInTheDocument();
  });
});
