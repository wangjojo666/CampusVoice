import { describe, expect, it, vi } from "vitest";

import { collectAllPages } from "@/lib/pagination";

describe("collectAllPages", () => {
  it("follows a stable total and offset contract until every unique item is loaded", async () => {
    const fetchPage = vi.fn(async ({ limit, offset }: { limit: number; offset: number }) => ({
      items: [1, 2, 3, 4, 5].slice(offset, offset + limit),
      total: 5,
    }));

    await expect(collectAllPages(fetchPage, { pageSize: 2, getKey: String })).resolves.toEqual([
      1, 2, 3, 4, 5,
    ]);
    expect(fetchPage.mock.calls).toEqual([
      [{ limit: 2, offset: 0 }],
      [{ limit: 2, offset: 2 }],
      [{ limit: 2, offset: 4 }],
    ]);
  });

  it("rejects an invalid page size before issuing a request", async () => {
    const fetchPage = vi.fn();
    await expect(collectAllPages(fetchPage, { pageSize: 0 })).rejects.toThrow(
      "pageSize must be a positive integer",
    );
    expect(fetchPage).not.toHaveBeenCalled();
  });

  it("fails closed when a later page repeats an item", async () => {
    const fetchPage = vi
      .fn()
      .mockResolvedValueOnce({ items: [{ id: "a" }, { id: "b" }], total: 3 })
      .mockResolvedValueOnce({ items: [{ id: "b" }], total: 3 });

    await expect(
      collectAllPages<{ id: string }>(fetchPage, { pageSize: 2, getKey: (item) => item.id }),
    ).rejects.toThrow("repeated an item");
  });

  it("fails closed when total changes or a page ends early", async () => {
    const changedTotal = vi
      .fn()
      .mockResolvedValueOnce({ items: [1, 2], total: 3 })
      .mockResolvedValueOnce({ items: [3], total: 4 });
    await expect(collectAllPages(changedTotal, { pageSize: 2 })).rejects.toThrow(
      "changed while it was loading",
    );

    const endedEarly = vi
      .fn()
      .mockResolvedValueOnce({ items: [1, 2], total: 3 })
      .mockResolvedValueOnce({ items: [], total: 3 });
    await expect(collectAllPages(endedEarly, { pageSize: 2 })).rejects.toThrow(
      "ended before the advertised total",
    );
  });

  it("stops a superseded collection before requesting another page", async () => {
    let current = true;
    const fetchPage = vi.fn(async () => {
      current = false;
      return { items: [1], total: 2 };
    });

    await expect(
      collectAllPages(fetchPage, { pageSize: 1, shouldContinue: () => current }),
    ).rejects.toThrow("superseded");
    expect(fetchPage).toHaveBeenCalledTimes(1);
  });
  it("stops after the first fully validated page that satisfies the caller", async () => {
    const fetchPage = vi
      .fn()
      .mockResolvedValueOnce({ items: [{ id: "a" }], total: 3 })
      .mockResolvedValueOnce({ items: [{ id: "target" }], total: 3 });

    await expect(
      collectAllPages<{ id: string }>(fetchPage, {
        pageSize: 1,
        getKey: (item) => item.id,
        shouldStop: (page) => page.some((item) => item.id === "target"),
      }),
    ).resolves.toEqual([{ id: "a" }, { id: "target" }]);
    expect(fetchPage).toHaveBeenCalledTimes(2);
  });
  it("rejects an over-total page before applying the caller's early-stop predicate", async () => {
    const fetchPage = vi.fn().mockResolvedValue({
      items: [{ id: "target" }],
      total: 0,
    });

    await expect(
      collectAllPages<{ id: string }>(fetchPage, {
        pageSize: 1,
        getKey: (item) => item.id,
        shouldStop: (page) => page.some((item) => item.id === "target"),
      }),
    ).rejects.toThrow("exceeded the advertised total");
  });
});
