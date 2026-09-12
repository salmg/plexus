/**
 * Storage that is allowed to be unavailable.
 *
 * A private window, a browser with site data blocked, or a page opened from
 * `file://` all make `localStorage` *throw* rather than return nothing — and
 * these get read in initialisers, so the throw would take the whole app down
 * before it paints. Remembering a panel or a theme is not worth that.
 *
 * Its own module because the store reads it too: the theme has to be restored
 * where the store is *created*, not in an effect afterwards. As an effect it
 * raced the effect that persists it, and the default overwrote the saved value
 * often enough that choosing light mode did not survive a reload.
 */

export const CONSOLE_KEY = "plexus.console-open";
export const THEME_KEY = "plexus.theme";

export function remembered(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

export function remember(key: string, value: string): void {
  try {
    localStorage.setItem(key, value);
  } catch {
    // Not being able to remember a panel is not worth telling anyone about.
  }
}
