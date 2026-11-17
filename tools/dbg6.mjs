import { chromium } from "playwright-core";
const b = await chromium.launch({ executablePath: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", headless: true });
const p = await b.newPage({ viewport: { width: 1440, height: 900 } });
p.on("pageerror", e => console.log("[pageerror]", String(e).slice(0, 300)));
p.on("console", m => { if (m.type() === "error") console.log("[console]", m.text().slice(0, 300)); });
p.on("response", async r => {
  if (r.url().includes("review_queue")) console.log("[net] review_queue →", r.status());
});
await p.goto("http://127.0.0.1:8017/ui/console.html#label", { waitUntil: "networkidle" });
await p.waitForTimeout(4000);
console.log("cards:", await p.locator("#lGrid .lcard").count());
console.log("progress:", await p.textContent("#lProgress"));
console.log("grid html:", (await p.locator("#lGrid").innerHTML()).slice(0, 200));
await b.close();
