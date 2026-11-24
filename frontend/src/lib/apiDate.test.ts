import { describe, expect, it } from "vitest";

import { parseApiDate } from "./apiDate";

describe("parseApiDate", () => {
  it("treats naive ISO as UTC", () => {
    const d = parseApiDate("2026-11-24T17:53:08.502512");
    expect(d).not.toBeNull();
    expect(d!.toISOString()).toBe("2026-11-24T17:53:08.502Z");
  });

  it("keeps explicit Z", () => {
    const d = parseApiDate("2026-11-24T17:53:08.502512Z");
    expect(d!.toISOString()).toBe("2026-11-24T17:53:08.502Z");
  });
});
