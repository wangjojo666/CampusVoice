import { defineConfig, devices } from "@playwright/test";

const channel = process.env.PLAYWRIGHT_CHANNEL;

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 2 : 4,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: "http://127.0.0.1:3100",
    locale: "zh-CN",
    timezoneId: "Asia/Shanghai",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },
  projects: channel
    ? [
        {
          name: channel,
          use: { ...devices["Desktop Chrome"], channel },
        },
      ]
    : [
        { name: "desktop-chromium", use: { ...devices["Desktop Chrome"] } },
        {
          name: "mobile-320-chromium",
          use: { ...devices["Pixel 7"], viewport: { width: 320, height: 720 } },
        },
        {
          name: "mobile-360-chromium",
          use: { ...devices["Pixel 7"], viewport: { width: 360, height: 800 } },
        },
        {
          name: "mobile-375-webkit",
          use: { ...devices["iPhone 13"], viewport: { width: 375, height: 812 } },
        },
        { name: "android-pixel-7", use: { ...devices["Pixel 7"] } },
        { name: "iphone-13-webkit", use: { ...devices["iPhone 13"] } },
        {
          name: "tablet-768-webkit",
          use: { ...devices["iPad (gen 7)"], viewport: { width: 768, height: 1024 } },
        },
      ],
  webServer: {
    command: "pnpm dev --hostname 127.0.0.1 --port 3100",
    url: "http://127.0.0.1:3100",
    env: {
      ...process.env,
      NEXT_PUBLIC_API_BASE_URL: "http://127.0.0.1:3100",
      NEXT_PUBLIC_ASR_WS_URL: "ws://127.0.0.1:3100/ws/asr",
    },
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
