import type { TransportationLeg } from "@/lib/types";

function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null
    ? (value as Record<string, unknown>)
    : null;
}

function firstText(
  value: Record<string, unknown>,
  keys: string[],
): string {
  for (const key of keys) {
    const item = value[key];
    if (item !== undefined && item !== null && String(item).trim()) {
      return String(item).trim();
    }
  }
  return "";
}

function firstNumber(
  value: Record<string, unknown>,
  keys: string[],
): number | null {
  for (const key of keys) {
    const item = value[key];
    if (typeof item === "number" && Number.isFinite(item)) {
      return item;
    }
    if (typeof item === "string") {
      const parsed = Number(item.replace(/[^\d.-]/g, ""));
      if (Number.isFinite(parsed)) {
        return parsed;
      }
    }
  }
  return null;
}

function firstSafeUrl(
  value: Record<string, unknown>,
  keys: string[],
): string {
  const candidate = firstText(value, keys);
  if (!candidate) return "";
  try {
    const url = new URL(candidate);
    return url.protocol === "https:" ? url.toString() : "";
  } catch {
    return "";
  }
}

export function normalizeTransportationLegs(
  value: unknown,
): TransportationLeg[] {
  if (!Array.isArray(value)) return [];

  return value.flatMap((item) => {
    const leg = asRecord(item);
    if (!leg) return [];

    return [{
      direction: firstText(leg, ["direction", "segment"]),
      method: firstText(leg, ["method", "mode"]),
      from: firstText(leg, [
        "from",
        "origin",
        "departure_point",
        "departure_station",
      ]),
      to: firstText(leg, [
        "to",
        "destination",
        "arrival_point",
        "arrival_station",
      ]),
      cost: firstNumber(leg, [
        "cost",
        "fare_jpy",
        "fare",
        "price_jpy",
        "price",
      ]),
      fareType: firstText(leg, [
        "fare_type",
        "ticket_type",
        "fare_basis",
      ]),
      sourceUrl: firstSafeUrl(leg, [
        "source_url",
        "fare_source_url",
        "url",
      ]),
      sourceTitle: firstText(leg, [
        "source_title",
        "fare_source_title",
        "title",
      ]),
    }];
  });
}

export function formatTransportationCost(
  cost: number | null | undefined,
): string {
  return typeof cost === "number" ? `¥${cost.toLocaleString()}` : "—";
}
