/** The pages' Content-Security-Policy (BACKLOG L16 S4, doc/SECURITY.md). src/proxy.ts builds it for every page
 *  request with a fresh nonce; Next puts that nonce on its own scripts, app/layout.tsx on the theme script.
 *  The API's responses carry their own, stricter policy (web/headers.py). */

/** The request header that hands the nonce from the proxy to the layout. */
export const NONCE_HEADER = "x-nonce";

// Outside sources the UI uses today. Both reach Google, so both are a privacy choice, not a technical need:
const GOOGLE_FONTS_CSS = "https://fonts.googleapis.com"; // the @import at the top of app/globals.css
const GOOGLE_FONTS_FILES = "https://fonts.gstatic.com";
// source pills (answer/MarkdownBody, chat/MessageExtras): google.com/s2/favicons answers with a redirect to tN.gstatic.com
const FAVICON_SOURCES = ["https://www.google.com", "https://*.gstatic.com"];

export function contentSecurityPolicy(nonce: string, dev: boolean): string {
  const directives: Record<string, string[]> = {
    "default-src": ["'self'"],
    // only scripts with this request's nonce, and the chunks they load. React's dev build uses eval for its
    // error stacks; the production build does not
    "script-src": ["'self'", `'nonce-${nonce}'`, "'strict-dynamic'", ...(dev ? ["'unsafe-eval'"] : [])],
    // React `style` props and the <style> inside a Mermaid SVG cannot carry a nonce; a style cannot run code
    "style-src": ["'self'", "'unsafe-inline'", GOOGLE_FONTS_CSS],
    "font-src": ["'self'", GOOGLE_FONTS_FILES],
    // data: the CAPTCHA picture; blob: upload previews and diagram export
    "img-src": ["'self'", "data:", "blob:", ...FAVICON_SOURCES],
    // the API through /api; in dev also Next's reload socket (same host)
    "connect-src": ["'self'"],
    "object-src": ["'none'"],
    "base-uri": ["'self'"],
    "form-action": ["'self'"],
    "frame-ancestors": ["'none'"],
  };
  // no upgrade-insecure-requests: the LAN reaches the app over plain http (S1b)
  return Object.entries(directives)
    .map(([name, sources]) => `${name} ${sources.join(" ")}`)
    .join("; ");
}
