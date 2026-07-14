import { expect, test } from "@playwright/test";

test("mock contract: v1 to v2 impact preview execute verify and group undo", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const expectNoHorizontalOverflow = async () => {
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= document.documentElement.clientWidth,
      ),
    ).toBe(true);
  };
  let planStatus = "ready";
  let planVersion = 1;
  const change = {
    id: "ncs-e2e",
    series_id: "nss-e2e",
    from_document_id: "doc-v1",
    to_document_id: "doc-v2",
    algorithm_version: "normalized-diff-v1",
    status: "ready",
    created_at: "2026-07-13T00:00:00Z",
    items: [
      {
        id: "nci-time",
        claim_key: "event.start_at",
        change_type: "changed",
        severity: "high",
        confidence: 0.98,
        review_state: "approved",
        before: {
          claim_id: "old-time",
          document_id: "doc-v1",
          chunk_id: "chunk-v1",
          value: { start: "09:00" },
          normalized_value: { iso: "2026-07-18T09:00:00+08:00" },
          evidence_text: "考试时间：2026-07-18 09:00–11:00",
          evidence_start: 20,
          evidence_end: 55,
        },
        after: {
          claim_id: "new-time",
          document_id: "doc-v2",
          chunk_id: "chunk-v2",
          value: { start: "14:00" },
          normalized_value: { iso: "2026-07-18T14:00:00+08:00" },
          evidence_text: "考试时间：2026-07-18 14:00–16:00",
          evidence_start: 20,
          evidence_end: 55,
        },
      },
    ],
  };
  const migrationItem = {
    id: "mpi-e2e",
    entity_type: "event",
    entity_id: "evt-exam",
    expected_version: 1,
    before: {
      title: "人工智能专业考试",
      start_at: "2026-07-18T09:00:00+08:00",
      end_at: "2026-07-18T11:00:00+08:00",
      location: "教学楼 A302",
    },
    after: {
      title: "人工智能专业考试",
      start_at: "2026-07-18T14:00:00+08:00",
      end_at: "2026-07-18T16:00:00+08:00",
      location: "教学楼 B205",
    },
    source_claim_ids: ["new-time"],
    verification: { verified: true },
    execute_verification: { operation: "execute", verified: true },
    undo_verification: {},
  };
  const plan = () => ({
    id: "mpl-e2e",
    change_set_id: "ncs-e2e",
    status: planStatus,
    risk_level: "low",
    required_confirmations: 1,
    conflicts: [],
    items: [migrationItem],
    verification: planStatus === "ready" ? {} : { verified: true },
    execute_receipt: planStatus === "ready" ? {} : { operation: "execute", status: "verified" },
    undo_receipt: planStatus === "undone" ? { operation: "undo", status: "undone" } : {},
    generation: 1,
    version: planVersion,
    executed_at: planStatus === "ready" ? null : "2026-07-13T00:01:00Z",
    undone_at: planStatus === "undone" ? "2026-07-13T00:02:00Z" : null,
  });

  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    if (path === "/api/settings" && request.method() === "GET") {
      await route.fulfill({
        json: {
          major: null,
          grade: null,
          current_courses: [],
          teacher_names: [],
          default_reminder_minutes: 30,
          timezone: "Asia/Shanghai",
          asr_provider: "disabled",
          asr_model: "",
          asr_device: "",
        },
      });
      return;
    }
    if (path === "/api/tasks" || path === "/api/events" || path === "/api/action-logs") {
      await route.fulfill({ json: { items: [], total: 0 } });
      return;
    }
    if (path === "/api/notice-radar" && request.method() === "GET") {
      await route.fulfill({
        json: {
          total: 1,
          items: [
            {
              card_type: "version_change",
              change_set_id: "ncs-e2e",
              series_id: "nss-e2e",
              document_id: "doc-v2",
              title: "2026 人工智能专业考试安排",
              from_revision: 1,
              to_revision: 2,
              change_count: 2,
              affected_tasks: 2,
              affected_events: 1,
              needs_review: false,
              deadline_at: null,
              applicability: "applicable",
              applicability_reason: "The explicit audience rule matches",
              message: "《2026 人工智能专业考试安排》已更新，影响 1 个日程和 2 个待办。",
              created_at: "2026-07-13T00:00:00Z",
            },
          ],
        },
      });
      return;
    }
    if (path === "/api/notice-radar/changes/ncs-e2e") {
      await route.fulfill({ json: change });
      return;
    }
    if (path === "/api/notice-radar/impacts") {
      await route.fulfill({
        json: {
          total: 1,
          items: [
            {
              id: "imp-e2e",
              change_item_id: "nci-time",
              entity_type: "event",
              entity_id: "evt-exam",
              entity_version: 1,
              reason: "event.start_at changed in the explicit successor notice",
              severity: "high",
              current_snapshot: migrationItem.before,
              proposed_patch: { start_at: migrationItem.after.start_at },
              recommended_action: "apply",
              requires_manual_review: false,
              status: "open",
              migration_plan_id: null,
            },
          ],
        },
      });
      return;
    }
    if (path.endsWith("/migration-preview")) {
      await route.fulfill({ json: plan() });
      return;
    }
    if (path === "/api/auth/write-challenges") {
      const body = request.postDataJSON() as { body?: { confirmation_stages?: number } };
      const required = body.body?.confirmation_stages ?? 1;
      await route.fulfill({
        json: {
          challenge: `challenge-stage-1-${required}`,
          stage: 1,
          required_stages: required,
          expires_at: "2026-07-13T01:00:00Z",
        },
      });
      return;
    }
    if (path === "/api/auth/write-challenges/advance") {
      await route.fulfill({
        json: {
          challenge: "challenge-stage-2",
          stage: 2,
          required_stages: 2,
          expires_at: "2026-07-13T01:00:00Z",
        },
      });
      return;
    }
    if (path === "/api/notice-radar/migrations/mpl-e2e/execute") {
      planStatus = "verified";
      planVersion = 2;
      await route.fulfill({
        json: {
          plan_id: "mpl-e2e",
          status: "verified",
          operation: "execute",
          verified_count: 1,
          total_count: 1,
          all_verified: true,
          items: [migrationItem],
          verified_at: "2026-07-13T00:01:00Z",
        },
      });
      return;
    }
    if (path === "/api/notice-radar/migrations/mpl-e2e/undo") {
      planStatus = "undone";
      planVersion = 3;
      await route.fulfill({
        json: {
          plan_id: "mpl-e2e",
          status: "undone",
          operation: "undo",
          verified_count: 1,
          total_count: 1,
          all_verified: true,
          items: [migrationItem],
          verified_at: "2026-07-13T00:02:00Z",
        },
      });
      return;
    }
    if (path === "/api/notice-radar/migrations/mpl-e2e") {
      await route.fulfill({ json: plan() });
      return;
    }
    await route.fulfill({ status: 404, json: { error: { code: "not_mocked" } } });
  });

  await page.goto("/");
  await expectNoHorizontalOverflow();
  await page.getByRole("link", { name: /2026 人工智能专业考试安排/ }).click();
  await expect(page.getByText("v1 → v2 结构化变化")).toBeVisible();
  await expect(page.getByText("考试时间：2026-07-18 09:00–11:00")).toBeVisible();
  await expectNoHorizontalOverflow();
  await page.getByRole("button", { name: /生成迁移预览/ }).click();
  await expect(page.getByText("确认前预览，不会写入真实安排")).toBeVisible();
  await expect(page.getByText(/Before ghost：旧安排/)).toBeVisible();
  await expect(page.getByText(/New block：建议新安排/)).toBeVisible();
  await expect(page.getByRole("link", { name: /开始时间 · 新版证据 · new-time/ })).toBeVisible();
  await expectNoHorizontalOverflow();
  await page.getByRole("button", { name: "确认迁移" }).click();
  await expect(page.getByText("1/1 项安排已更新并通过数据库验证")).toBeVisible();
  await expectNoHorizontalOverflow();
  await page.getByRole("button", { name: "撤销整个迁移" }).click();
  await page.getByRole("button", { name: /再次确认撤销整组迁移/ }).click();
  await expect(page.getByText("1/1 项安排已恢复并通过数据库验证")).toBeVisible();
  await expectNoHorizontalOverflow();
});
