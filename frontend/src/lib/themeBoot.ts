/** Theme constants + the before-paint script, free of React so the server layout can import them
 *  (src/lib/theme.ts holds the client hook and setter that share these). */
export const THEME_KEY = "multeagent.theme";
export const LIGHT_QUERY = "(prefers-color-scheme: light)";

/** Runs inline in <head> before first paint (layout.tsx) — same rule as theme.ts applyTheme, so there is no
 *  flash of the wrong theme on load. A string, because it must run before React does. */
export const THEME_BOOT_SCRIPT = `(function(){try{var p=localStorage.getItem("${THEME_KEY}");var l=p==="light"||(p!=="dark"&&matchMedia("${LIGHT_QUERY}").matches);document.documentElement.dataset.theme=l?"light":"dark"}catch(e){}})();`;
