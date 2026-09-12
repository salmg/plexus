/**
 * Translation, without a framework.
 *
 * Three things shaped this.
 *
 * **English stays where it already is.** Node titles, summaries and caveats
 * live in the catalogue, which is also what `plexus/schema/catalogue.json` exports to
 * the Python side. Copying them into an English dictionary would give the
 * project two canonical Englishes to keep in step, so instead a lookup misses
 * and falls back to the catalogue's own text. Only Spanish carries node keys.
 *
 * **A missing translation shows English, not a key.** `es` falls back to `en`,
 * which falls back to the key itself. A half-translated build is usable; a
 * build showing `inspector.checks.none` is not.
 *
 * **`t` works outside React.** The validator is a plain module and produces
 * sentences people read, so it cannot require a hook. The locale therefore
 * lives in a store that can be read synchronously; components subscribe with
 * `useT` so they re-render, and anything memoised on the outside takes the
 * locale as a dependency.
 */

import { useMemo } from "react";
import { create } from "zustand";

import { en } from "./en";
import { es } from "./es";

export const LOCALES = ["en", "es"] as const;
export type Locale = (typeof LOCALES)[number];

export const LOCALE_NAMES: Record<Locale, string> = {
  en: "English",
  es: "Español",
};

/** Every key the chrome uses. Catalogue keys are looser — see `catalogueText`. */
import type { MessageKey } from "./en";

export type { MessageKey };

export type Vars = Record<string, string | number>;

const DICTIONARIES: Record<Locale, Partial<Record<string, string>>> = { en, es };

const STORAGE_KEY = "plexus.locale";

function detect(): Locale {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved && (LOCALES as readonly string[]).includes(saved)) return saved as Locale;
  } catch {
    // Private mode, or storage blocked. The browser's own preference will do.
  }
  const preferred =
    typeof navigator !== "undefined" ? navigator.languages ?? [navigator.language] : [];
  for (const tag of preferred) {
    const base = String(tag).toLowerCase().split("-")[0];
    if ((LOCALES as readonly string[]).includes(base)) return base as Locale;
  }
  return "en";
}

interface LocaleState {
  locale: Locale;
  setLocale: (locale: Locale) => void;
}

export const useLocaleStore = create<LocaleState>((set) => ({
  locale: detect(),
  setLocale: (locale) => {
    try {
      localStorage.setItem(STORAGE_KEY, locale);
    } catch {
      // Not being able to remember the choice is not a reason to refuse it.
    }
    document.documentElement.lang = locale;
    set({ locale });
  },
}));

export function locale(): Locale {
  return useLocaleStore.getState().locale;
}

function fill(template: string, vars?: Vars): string {
  if (!vars) return template;
  return template.replace(/\{(\w+)\}/g, (whole, name: string) =>
    name in vars ? String(vars[name]) : whole,
  );
}

/**
 * Look a message up in the current locale.
 *
 * `key` is typed for the chrome and loose for catalogue text, which is keyed
 * by node and field names that only exist at run time.
 */
export function t(key: MessageKey | string, vars?: Vars): string {
  const current = locale();
  const found = DICTIONARIES[current][key] ?? DICTIONARIES.en[key];
  return found === undefined ? String(key) : fill(found, vars);
}

/**
 * A counted message.
 *
 * English and Spanish both split one from many, and both put the number in a
 * different place in the sentence, so each form is a whole phrase rather than
 * a number glued to a word.
 */
export function tn(key: MessageKey | string, count: number, vars?: Vars): string {
  const form = count === 1 ? `${key}_one` : `${key}_other`;
  // `count` first, so a caller that formats it — `toLocaleString()` for the
  // thousands separator the reader expects — keeps its version. Spreading it
  // last overwrote that with the raw number, so every grouped count on a node
  // card was quietly ungrouped.
  return t(form, { count, ...vars });
}

/**
 * Catalogue text, translated when a translation exists.
 *
 * The fallback is the English already in the catalogue, so nothing has to be
 * duplicated to add a language — an untranslated node reads in English inside
 * an otherwise Spanish interface, which is the honest outcome.
 */
export function catalogueText(key: string, fallback: string): string {
  const current = locale();
  if (current === "en") return fallback;
  return DICTIONARIES[current][key] ?? fallback;
}

/**
 * Subscribe a component to the locale.
 *
 * The returned function changes identity with the locale, and that is
 * load-bearing rather than incidental: several places memoise work that
 * produces language — the validator's sentences, the palette's filtering — and
 * they list `t` as a dependency. Returning the module-level `t` would
 * subscribe the component but leave every one of those memos stale, showing
 * the previous language until something else happened to invalidate them.
 */
export function useT(): typeof t {
  const current = useLocaleStore((s) => s.locale);
  return useMemo(() => {
    const bound: typeof t = (key, vars) => t(key, vars);
    return bound;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [current]);
}


/**
 * `tn`, for a memo.
 *
 * Same trick as `useT`, and it exists for the same reason: a memo over counted
 * output could not follow the documented rule — "put `t` in its dependency
 * array" — because there was no `tn` whose identity changed with the locale.
 * The canvas's edge labels are counted, so their memo listed neither, and
 * every edge kept its English count until something else invalidated it.
 */
export function useTn(): typeof tn {
  const current = useLocaleStore((s) => s.locale);
  return useMemo(() => {
    const bound: typeof tn = (key, count, vars) => tn(key, count, vars);
    return bound;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [current]);
}
export function useLocale(): [Locale, (locale: Locale) => void] {
  const current = useLocaleStore((s) => s.locale);
  const setLocale = useLocaleStore((s) => s.setLocale);
  return [current, setLocale];
}

/**
 * "A and B" in the reader's language.
 *
 * `join(" and ")` put an English word inside a translated sentence. Spanish
 * needs "y", and needs "e" before a word starting with i- or hi- — which
 * `Intl.ListFormat` knows and a lookup table would get wrong.
 */
export function andList(items: string[]): string {
  try {
    return new Intl.ListFormat(locale(), {
      style: "long",
      type: "conjunction",
    }).format(items);
  } catch {
    // A runtime without Intl.ListFormat. Rare, and not worth failing a check
    // over.
    return items.join(", ");
  }
}
