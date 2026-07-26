/**
 * Background: Memory-related backend endpoints return two shapes—list endpoints return
 * a flat array of memory objects, while detail/create/update return a {memory: {...}} envelope,
 * and search/related return {results|related: [{memory: {...}, score, match_type}]}.
 * Design intent: Normalize both shapes into the flat structure used by the frontend,
 * so the page layer only faces one data shape.
 * Key constraint: Any missing field or shape mismatch returns null/empty instead of throwing.
 */
import type { MemoryDetail, MemorySummary, SearchResultItem } from "./types";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

/** Detail/create/update endpoints: extracts the memory body from both {memory: {...}} and bare objects */
export function unwrapMemory(res: unknown): MemoryDetail | null {
  if (!isRecord(res)) return null;
  if (isRecord(res.memory)) return res.memory as MemoryDetail;
  return res as MemoryDetail;
}

/**
 * Search/related memory endpoints: flattens {memory, score, match_type} into a single card data item.
 * keys specifies the list field names to try in priority order.
 */
export function flattenResults(res: unknown, keys: string[] = ["results", "related", "memories"]): SearchResultItem[] {
  let list: unknown = Array.isArray(res) ? res : undefined;
  if (!list && isRecord(res)) {
    for (const key of keys) {
      if (Array.isArray(res[key])) {
        list = res[key];
        break;
      }
    }
  }
  if (!Array.isArray(list)) return [];

  return list.map((item) => {
    if (!isRecord(item)) return {} as SearchResultItem;
    if (!isRecord(item.memory)) return item as SearchResultItem;
    return {
      ...(item.memory as MemorySummary),
      score: typeof item.score === "number" ? item.score : undefined,
      match: typeof item.match_type === "string" ? item.match_type : undefined,
    };
  });
}
