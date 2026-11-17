import { chromium } from "playwright-core";
const B = "http://127.0.0.1:8017";
const b = await chromium.launch({ executablePath: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", headless: true });
const p = await b.newPage({ viewport: { width: 1440, height: 900 } });
const errs = [];
p.on("pageerror", e => errs.push("[pageerror] " + String(e).slice(0, 200)));
p.on("console", m => { if (m.type() === "error") errs.push("[console] " + m.text().slice(0, 200)); });

const shot = async (name) => { await p.waitForTimeout(900); await p.screenshot({ path: `/tmp/audit/${name}.png` }); };

await p.goto(`${B}/ui/console.html?p=1#overview`, { waitUntil: "networkidle" });
await p.waitForSelector("#kpis .kpi"); await shot("01_overview");

await p.goto(`${B}/ui/console.html?p=2#dataset`, { waitUntil: "networkidle" });
await p.waitForSelector("#dsBatches .ds-batch", { timeout: 8000 }).catch(()=>{}); await shot("02_dataset");

await p.goto(`${B}/ui/console.html?p=3#mistakes`, { waitUntil: "networkidle" });
await p.waitForTimeout(1200); await shot("03_mistakes");

await p.goto(`${B}/ui/console.html?p=4#label`, { waitUntil: "networkidle" });
await p.waitForSelector("#lGrid .lcard", { timeout: 15000 }); await shot("04_label");

await p.goto(`${B}/ui/console.html?p=5#model`, { waitUntil: "networkidle" });
await p.waitForSelector("#modelTable table"); await shot("05_model");

// 在线识别：修复类，验证三格显示
await p.goto(`${B}/ui/console.html?p=6#infer`, { waitUntil: "networkidle" });
await p.click('#catSeg button[data-cat="repair"]');
await p.waitForTimeout(300);
const dir = "/Users/deepway/data/spmcv/data/SPM系统AI识别训练照片_新增_20261111-20261113/02_故障及修复照片/01_桥放油";
const { readdir } = await import("node:fs/promises");
const fs = (await readdir(dir)).filter(f => /\.(png|jpg)$/i.test(f)).slice(0, 1).map(f => `${dir}/${f}`);
await p.setInputFiles("#files", fs);
await p.waitForSelector("#inferImgWrap:not([hidden])");
await p.click("#run");
await p.waitForSelector("#out .card", { timeout: 180000 });
await shot("06_infer_repair");

console.log(errs.length ? errs.join("\n") : "无 JS 报错");
await b.close();
