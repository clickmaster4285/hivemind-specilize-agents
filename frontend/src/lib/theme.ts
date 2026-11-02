"use client";

import { useSyncExternalStore } from "react";

import { LIGHT_QUERY, THEME_KEY } from "@/lib/themeBoot";

/** Colour theme: "system" follows the OS setting (live); "light" / "dark" pin it. The choice is per device,
 *  so it lives in localStorage; the resolved theme is <html data-theme>, which globals.css keys off. The
 *  before-paint script that sets it on load lives in themeBoot.ts (the server layout can't import hooks). */
export type ThemePref = "system" | "light" | "dark";

const CHANGE_EVENT = "multeagent:theme";

function readPref(): ThemePref {
  try {
    const v = localStorage.getItem(THEME_KEY);
    return v === "light" || v === "dark" ? v : "system";
  } catch {
    return "system";
  }
}

function applyTheme(pref: ThemePref): void {
  const light = pref === "light" || (pref === "system" && window.matchMedia(LIGHT_QUERY).matches);
  document.documentElement.dataset.theme = light ? "light" : "dark";
}

export function setThemePref(pref: ThemePref): void {
  try {
    if (pref === "system") localStorage.removeItem(THEME_KEY);
    else localStorage.setItem(THEME_KEY, pref);
  } catch {
    // storage blocked: the theme still applies for this page view
  }
  applyTheme(pref);
  window.dispatchEvent(new Event(CHANGE_EVENT));
}

function subscribe(onChange: () => void): () => void {
  const media = window.matchMedia(LIGHT_QUERY);
  const onSystem = () => {
    if (readPref() === "system") applyTheme("system"); // follow the OS only while not pinned
    onChange();
  };
  const onOtherTab = () => {
    applyTheme(readPref()); // another tab changed the choice
    onChange();
  };
  media.addEventListener("change", onSystem);
  window.addEventListener(CHANGE_EVENT, onChange);
  window.addEventListener("storage", onOtherTab);
  return () => {
    media.removeEventListener("change", onSystem);
    window.removeEventListener(CHANGE_EVENT, onChange);
    window.removeEventListener("storage", onOtherTab);
  };
}

/** The saved theme choice ("system" during server render). */
export function useThemePref(): ThemePref {
  return useSyncExternalStore(subscribe, readPref, () => "system");
}
