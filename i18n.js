// Board translations. A key is the English text exactly as index.html writes it (markup text, title/
// placeholder/aria-label attributes, and every t("...") call); a key the dictionary lacks shows the English.
// The `fr` object between the fr-start / fr-end markers must stay strict JSON: tests/test_i18n.py parses it.
"use strict";
const I18N_LANGS = { en: "English", fr: "Français" };
let LANG = "en";
try { LANG = I18N_LANGS[localStorage.getItem("task-board:lang")] ? localStorage.getItem("task-board:lang") : "en"; } catch (_) {}
document.documentElement.lang = LANG;

const I18N = {
  fr: /* fr-start */ {
  } /* fr-end */
};

// t("Move to {col}", { col }) — {name} placeholders are filled after lookup, so the key stays one fixed string.
function t(s, vars) {
  const d = I18N[LANG];
  let r = d && d[s] != null ? d[s] : s;
  if (vars) for (const k in vars) r = r.split("{" + k + "}").join(vars[k]);
  return r;
}

// Static markup: every text node and title/placeholder/aria-label attribute under `root` is looked up as a key.
// An element with data-i18n is looked up by its whole inner markup instead (text mixed with <code> or <b>).
const I18N_ATTRS = ["title", "placeholder", "aria-label"];
function translateDom(root) {
  if (LANG === "en") return;
  const d = I18N[LANG];
  const w = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT, {
    acceptNode: (n) => {
      if (n.nodeType === 1 && (n.tagName === "SCRIPT" || n.tagName === "STYLE")) return NodeFilter.FILTER_REJECT;
      if (n.nodeType === 3 && n.parentElement && n.parentElement.closest("[data-i18n]")) return NodeFilter.FILTER_REJECT;
      return NodeFilter.FILTER_ACCEPT;
    },
  });
  const els = [];
  for (let n = w.nextNode(); n; n = w.nextNode()) {
    if (n.nodeType === 1) { els.push(n); continue; }
    const s = n.nodeValue, k = s.trim();
    if (k && d[k] != null) n.nodeValue = s.replace(k, d[k]);
  }
  for (const el of els) {
    for (const a of I18N_ATTRS) if (el.hasAttribute(a)) el.setAttribute(a, t(el.getAttribute(a)));
    if (el.hasAttribute("data-i18n")) { const k = el.innerHTML.trim(); if (d[k] != null) el.innerHTML = d[k]; }
  }
}
