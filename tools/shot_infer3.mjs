import { chromium } from "playwright-core";
const b = await chromium.launch({ executablePath: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", headless: true });
const p = await b.newPage({ viewport: { width: 1440, height: 900 } });
await p.goto("http://127.0.0.1:8017/ui/console.html#infer", { waitUntil: "networkidle" });
await p.setInputFiles("#files", ["/tmp/frame_vin.png"]);
await p.waitForSelector("#inferImgWrap:not([hidden])");
await p.click("#run");
await p.waitForSelector("#out .card", { timeout: 120000 });
await p.waitForTimeout(800);
await p.screenshot({ path: "/tmp/ui_shots/14_infer_compact.png" });
// 展开逐张 + OCR 原文
await p.click(".photo-details summary").catch(()=>{});
await p.waitForTimeout(300);
await p.click(".ocr-details summary").catch(()=>{});
await p.waitForTimeout(400);
await p.screenshot({ path: "/tmp/ui_shots/15_ocr_text.png" });
console.log("ok");
await b.close();
