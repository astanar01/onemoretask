// node shot_list.mjs <port> <out.png> <width> [height]
import { chromium } from "playwright-core";
const [port, out, w, h = "900"] = process.argv.slice(2);
const browser = await chromium.launch({ channel: "chrome", headless: true });
const page = await browser.newPage({ viewport: { width: +w, height: +h } });
const errors = [];
page.on("pageerror", (e) => errors.push(e.message));
await page.addInitScript(() => { localStorage.setItem("task-board:view", "list"); });
await page.goto(`http://127.0.0.1:${port}/`);
await page.waitForSelector(".lcard .title");
await page.waitForTimeout(500);
await page.evaluate(() => { openDetail("demo1"); showTab("conversation"); });
await page.waitForTimeout(500);
const m = await page.evaluate(() => {
  const r = (s) => { const e = document.querySelector(s); if (!e) return null; const b = e.getBoundingClientRect(); return [Math.round(b.left), Math.round(b.top), Math.round(b.width), Math.round(b.height)]; };
  return { listView: document.body.classList.contains("view-list"), panelDisplay: getComputedStyle(document.querySelector("#panel")).display,
    board: r("#board"), panel: r("#panel"), side: r(".side"), main: r(".main") };
});
console.log(JSON.stringify(m), errors.length ? "ERRORS " + errors.join("; ") : "no page errors");
await page.screenshot({ path: out });
await browser.close();
