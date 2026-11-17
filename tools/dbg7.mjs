import { chromium } from "playwright-core";
const B = "http://127.0.0.1:8017";
const b = await chromium.launch({ executablePath: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", headless: true });
const p = await b.newPage({ viewport: { width: 1440, height: 900 } });
const errs = [];
p.on("pageerror", e => errs.push("[pageerror] " + String(e).slice(0, 200)));
p.on("console", m => { if (m.type() === "error") errs.push("[console] " + m.text().slice(0, 200)); });
p.on("dialog", async d => { console.log("[dialog]", d.message().slice(0, 80)); await d.dismiss(); });

// 1) 数据集上传
await p.goto(`${B}/ui/console.html?x=1#dataset`, { waitUntil: "networkidle" });
await p.setInputFiles("#dsFile", "/tmp/test_upload.zip");
await p.waitForFunction(() => (document.querySelector("#dsResult")?.textContent || "").includes("已导入"), { timeout: 60000 })
  .catch(() => console.log("上传结果未出现"));
console.log("upload:", (await p.textContent("#dsResult")).slice(0, 80));
await p.screenshot({ path: "/tmp/audit/20_upload.png" });

// 2) 错误样本：全选 + 加入改标计划
await p.goto(`${B}/ui/console.html?x=2#mistakes`, { waitUntil: "networkidle" });
await p.waitForSelector("#mGrid .lcard", { timeout: 15000 });
await p.click("#mSelectAll");
await p.waitForTimeout(400);
const relabelDisabled = await p.locator("#mRelabel").isDisabled();
console.log("全选后 加入改标计划 disabled =", relabelDisabled);
await p.click("#mRelabel");
await p.waitForTimeout(1500);
console.log("relabel btn:", await p.textContent("#mRelabel"));
await p.screenshot({ path: "/tmp/audit/21_batch.png" });

// 3) 模型版本切换（取消）
await p.goto(`${B}/ui/console.html?x=3#model`, { waitUntil: "networkidle" });
await p.waitForSelector("#modelTable table");
const btn = p.locator("#modelTable button").first();
if (await btn.count()) {
  await btn.click().catch(e => console.log("click err", String(e).slice(0, 80)));
  await p.waitForTimeout(600);
}
console.log("切换后 active:", await p.textContent("#modelTable .pill.ok").catch(() => "?"));

console.log(errs.length ? "错误:\n" + errs.join("\n") : "无 JS 报错");
await b.close();
