export const POPOVER_MAX_WIDTH = 420;
export const POPOVER_VIEWPORT_PADDING = 12;
export const POPOVER_ANCHOR_GAP = 6;

/** Right-align a popover under its anchor, then clamp the whole panel into the viewport. */
export function anchorPopover(
  anchor: { right: number; bottom: number },
  viewportWidth: number,
): { top: number; left: number; width: number } {
  if (viewportWidth < POPOVER_VIEWPORT_PADDING * 2) {
    throw new RangeError(`anchorPopover: viewport width ${viewportWidth} leaves no room for a panel`);
  }
  const width = Math.min(POPOVER_MAX_WIDTH, viewportWidth - POPOVER_VIEWPORT_PADDING * 2);
  const left = Math.max(
    POPOVER_VIEWPORT_PADDING,
    Math.min(anchor.right - width, viewportWidth - width - POPOVER_VIEWPORT_PADDING),
  );
  return { top: anchor.bottom + POPOVER_ANCHOR_GAP, left, width };
}
