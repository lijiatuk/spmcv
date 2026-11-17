import { chromium } from "playwright-core";
const b = await chromium.launch({ executablePath: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", headless: true });
const p = await b.newPage({ viewport: { width: 1440, height: 900 } });
await p.goto("http://127.0.0.1:8017/ui/console.html", { waitUntil: "networkidle" });
await p.click('.nav-item[data-view="dataset"]');
await p.waitForSelector("#dsBatches .ds-batch", { timeout: 10000 });
await p.screenshot({ path: "/tmp/ui_shots/10_dataset.png" });
// 点自动标注
await p.click('#dsBatches .ds-batch button');
await p.waitForFunction(() => (document.querySelector('.ds-batch .stat')?.textContent || '').includes('已标'), { timeout: 60000 });
await p.screenshot({ path: "/tmp/ui_shots/11_autolabel.png" });
console.log("stat:", await p.textContent('.ds-batch .stat'));
await b.close();
