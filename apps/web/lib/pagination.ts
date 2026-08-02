import type { ListResponse } from "@campusvoice/shared-types";

type PageRequest = { limit: number; offset: number };

type CollectPagesOptions<T> = {
  pageSize?: number;
  maxPages?: number;
  getKey?: (item: T) => string;
  shouldContinue?: () => boolean;
  shouldStop?: (pageItems: readonly T[], collectedItems: readonly T[]) => boolean;
};

export async function collectAllPages<T>(
  fetchPage: (request: PageRequest) => Promise<ListResponse<T>>,
  {
    pageSize = 500,
    maxPages = 1_000,
    getKey,
    shouldContinue = () => true,
    shouldStop,
  }: CollectPagesOptions<T> = {},
) {
  if (!Number.isInteger(pageSize) || pageSize < 1) {
    throw new RangeError("pageSize must be a positive integer");
  }
  if (!Number.isInteger(maxPages) || maxPages < 1) {
    throw new RangeError("maxPages must be a positive integer");
  }

  const items: T[] = [];
  const seenKeys = new Set<string>();
  let expectedTotal: number | null = null;
  let offset = 0;

  for (let pageNumber = 0; pageNumber < maxPages; pageNumber += 1) {
    if (!shouldContinue()) throw new Error("Paginated request was superseded");
    const page = await fetchPage({ limit: pageSize, offset });
    if (!shouldContinue()) throw new Error("Paginated request was superseded");
    if (!Number.isInteger(page.total) || page.total < 0) {
      throw new Error("Paginated response has an invalid total");
    }
    if (page.items.length > pageSize) {
      throw new Error("Paginated response exceeded the requested page size");
    }

    expectedTotal ??= page.total;
    if (page.total !== expectedTotal) {
      throw new Error("Paginated collection changed while it was loading");
    }
    if (page.items.length === 0) {
      if (offset === expectedTotal) return items;
      throw new Error("Paginated response ended before the advertised total");
    }

    for (const item of page.items) {
      if (getKey) {
        const key = getKey(item);
        if (seenKeys.has(key)) {
          throw new Error("Paginated response repeated an item across pages");
        }
        seenKeys.add(key);
      }
      items.push(item);
    }
    offset += page.items.length;
    if (offset > expectedTotal) {
      throw new Error("Paginated response exceeded the advertised total");
    }
    if (shouldStop?.(page.items, items)) return items;
    if (offset === expectedTotal) return items;
  }

  throw new Error("Paginated response exceeded the safe page limit");
}
