import { chromium } from "playwright-core";
const b = await chromium.launch({ executablePath: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", headless: true });
const p = await b.newPage();
p.on("pageerror", e => console.log("[err]", String(e).slice(0,200)));
await p.goto("http://127.0.0.1:8017/ui/console.html", { waitUntil: "networkidle" });
await p.waitForTimeout(1500);
console.log("foot:", await p.textContent(".sidebar .foot"));
await b.close();
