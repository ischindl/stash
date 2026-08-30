import { describe, expect, it } from "vitest";

import type { User } from "./types";
import { showToolsAndChat } from "./flags";

function userWith(showToolsAndChatFlag: boolean): User {
  return { show_tools_and_chat: showToolsAndChatFlag } as User;
}

describe("showToolsAndChat", () => {
  // The old domain set lived here; visibility is now decided server-side
  // (`/users/me` + TOOLS_AND_CHAT_DOMAINS), so the flag must not re-derive
  // anything from the email.
  it("follows the server flag, not the email domain", () => {
    const flagged = { ...userWith(true), email: "x@anything.example" };
    expect(showToolsAndChat(flagged)).toBe(true);
    expect(showToolsAndChat({ ...userWith(false), email: "x@ferganalabs.com" })).toBe(false);
  });

  it("is false before the user loads", () => {
    expect(showToolsAndChat(null)).toBe(false);
    expect(showToolsAndChat(undefined)).toBe(false);
  });
});
