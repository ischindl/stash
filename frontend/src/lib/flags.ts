import type { User } from "./types";

// Tools and Chat are cut from the product surface (Aug 2026 focus pass); the
// surviving domains are operator config (backend TOOLS_AND_CHAT_DOMAINS).
// /users/me computes `show_tools_and_chat`, so the rail here and the route
// gates can never disagree with the server.
export function showToolsAndChat(user: User | null | undefined): boolean {
  return user?.show_tools_and_chat ?? false;
}
