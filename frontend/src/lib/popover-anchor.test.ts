import { describe, expect, it } from "vitest";

import {
  POPOVER_ANCHOR_GAP,
  POPOVER_MAX_WIDTH,
  POPOVER_VIEWPORT_PADDING,
  anchorPopover,
} from "./popover-anchor";

describe("anchorPopover", () => {
  it("pulls the STAS-360 field case back inside a 420px viewport", () => {
    const { top, left, width } = anchorPopover({ right: 181, bottom: 50 }, 420);
    expect(left).toBeGreaterThanOrEqual(0);
    expect(left + width).toBeLessThanOrEqual(420);
    expect(width).toBe(Math.min(POPOVER_MAX_WIDTH, 420 - POPOVER_VIEWPORT_PADDING * 2));
    expect(top).toBe(50 + POPOVER_ANCHOR_GAP);
  });

  it("keeps every viewport inside its edges and stays right-aligned when there is room", () => {
    for (const vw of [320, 375, 420, 768, 1024, 1440]) {
      for (const right of [40, 181, 420, 1200, vw - POPOVER_VIEWPORT_PADDING]) {
        const { left, width } = anchorPopover({ right, bottom: 50 }, vw);
        expect(left).toBeGreaterThanOrEqual(POPOVER_VIEWPORT_PADDING);
        expect(left + width).toBeLessThanOrEqual(vw - POPOVER_VIEWPORT_PADDING);
        expect(width).toBeLessThanOrEqual(POPOVER_MAX_WIDTH);
      }
    }
    // Desktop parity: a right-edge trigger keeps today's right-aligned position.
    expect(anchorPopover({ right: 1428, bottom: 50 }, 1440)).toEqual({
      top: 56,
      left: 1008,
      width: 420,
    });
  });

  it("refuses an absurd viewport instead of inventing a width", () => {
    expect(() => anchorPopover({ right: 10, bottom: 50 }, 20)).toThrow(RangeError);
  });
});
