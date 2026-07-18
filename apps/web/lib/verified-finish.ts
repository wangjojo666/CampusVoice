import type { VerificationResult } from "@campusvoice/shared-types";

export type VerifiedFinishKind = "execute" | "undo";

export type VerifiedFinishEvent = {
  id: number;
  kind: VerifiedFinishKind;
};

let nextVerifiedFinishId = 0;

export function createVerifiedFinishEvent(
  result: VerificationResult,
  kind: VerifiedFinishKind,
): VerifiedFinishEvent | null {
  if (!result.success) return null;
  nextVerifiedFinishId += 1;
  return { id: nextVerifiedFinishId, kind };
}
