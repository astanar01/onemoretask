// node shot.mjs <port> <out.png> [theme]
import { chromium } from "playwright-core";
const [port, out, theme = "light"] = process.argv.slice(2);
const browser = await chromium.launch({ channel: "chrome", headless: true });
const page = await browser.newPage({ viewport: { width: 1500, height: 2600 }, colorScheme: theme });
const errors = [];
page.on("pageerror", (e) => errors.push(e.message));
await page.addInitScript((th) => { localStorage.setItem("task-board:theme", th); }, theme);
await page.goto(`http://127.0.0.1:${port}/`);
await page.waitForSelector(".card .title");
await page.waitForTimeout(600);
await page.evaluate(() => { openDetail("demo1"); showTab("conversation"); });
await page.waitForTimeout(500);
const m = await page.evaluate(() => {
  const li = [...document.querySelectorAll("#d-log li")];
  const g = (sel) => { const e = document.querySelector(sel); return e ? getComputedStyle(e).fontSize + "/" + getComputedStyle(e).lineHeight : null; };
  return { innerWidth, entries: li.length, h1: g("#d-log .md .h1"), h2: g("#d-log .md .h2"), h3: g("#d-log .md .h3"), body: g("#d-log li .msg"),
    table: !!document.querySelector("#d-log table"), pre: !!document.querySelector("#d-log pre"), logHeight: document.querySelector("#d-log").scrollHeight };
});
console.log(JSON.stringify(m), errors.length ? "ERRORS " + errors.join("; ") : "no page errors");
const body = page.locator("#d-body");
await page.evaluate(() => { const s = document.querySelector("#d-body"); s.scrollTop = 0; });
await body.screenshot({ path: out });
await browser.close();
