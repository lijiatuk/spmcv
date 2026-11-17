/**
 * 浏览器回归测试：用真实 Chrome 点一遍界面，出问题直接截图。
 *
 * 为什么需要：前端已经出过两次「语法通过、接口正常、但页面是坏的」——
 * 一次是删掉了 paintBoxes（框不画），一次是标签截断成全一样。
 * 只跑 curl 和 node --check 抓不到这类问题。
 *
 * 用法：
 *   node tools/ui_smoke.mjs                 # 跑全部，截图到 /tmp/ui_shots
 *   BASE=http://10.50.208.121:8017 node tools/ui_smoke.mjs
 */
import { chromium } from "playwright-core";
import { mkdir, writeFile } from "node:fs/promises";

const BASE = process.env.BASE || "http://127.0.0.1:8017";
const OUT = process.env.OUT || "/tmp/ui_shots";
const CHROME =
  process.env.CHROME || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";

const results = [];
let browser;
let page;

async function shot(page, name) {
  await page.screenshot({ path: `${OUT}/${name}.png`, fullPage: false });
}

async function step(name, fn) {
  const t0 = Date.now();
  // 机器被其它任务占满时，纯网络等待类的步骤会偶发超时。
  // 重试一次：真 bug 会连挂两次，基础设施抖动则能救回来。
  for (let attempt = 1; attempt <= 2; attempt++) {
    try {
      await fn();
      const ms = Date.now() - t0;
      results.push({ name, ok: true, ms, attempt });
      console.log(`  ✅ ${name} (${ms}ms${attempt > 1 ? "，重试后通过" : ""})`);
      return;
    } catch (err) {
      if (attempt === 2) {
        results.push({ name, ok: false, ms: Date.now() - t0, error: String(err).slice(0, 300) });
        console.log(`  ❌ ${name}: ${String(err).slice(0, 160)}`);
        try {
          const pages = browser ? (await browser.contexts())[0]?.pages() : [];
          if (pages && pages[0]) await shot(pages[0], `FAIL_${name}`);
        } catch {}
      } else {
        console.log(`  ↻ ${name} 首次失败，重试… (${String(err).slice(0, 90)})`);
        // 失败后页面可能停在弹窗/半渲染状态，直接卡住后续步骤。先复位再重试。
        try {
          await page.keyboard.press("Escape");
          await page.goto(`${BASE}/ui/console.html#overview`, { waitUntil: "networkidle" });
        } catch {}
        await page.waitForTimeout(800);
      }
    }
  }
}

async function main() {
  await mkdir(OUT, { recursive: true });
  browser = await chromium.launch({ executablePath: CHROME, headless: true });
  page = await browser.newPage({ viewport: { width: 1440, height: 900 } });

  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });

  await page.goto(`${BASE}/ui/console.html`, { waitUntil: "networkidle" });

  await step("总览加载 + KPI", async () => {
    await page.waitForSelector("#kpis .kpi", { timeout: 8000 });
    const n = await page.locator("#kpis .kpi").count();
    if (n < 4) throw new Error(`KPI 卡片只有 ${n} 个`);
    await shot(page, "01_overview");
  });

  await step("准确率图表有柱子", async () => {
    await page.waitForSelector("#accChart .chart", { timeout: 8000 });
    const bars = await page.locator("#accChart .chart rect.bar").count();
    if (bars < 10) throw new Error(`柱子只有 ${bars} 根，应该是 12`);
    await shot(page, "02_chart");
  });

  await step("标注网格：卡片 + 图上框 + 改标签落库", async () => {
    await page.click('.nav-item[data-view="label"]');
    await page.waitForSelector("#lGrid .lcard", { timeout: 15000 });
    await page.waitForFunction(
      () => document.querySelectorAll("#lGrid .lcard .bx").length > 0,
      { timeout: 20000 },
    );
    await shot(page, "06_label");

    const before = (await (await fetch(`${BASE}/viz/api/annotations?limit=2000`)).json()).items || [];
    const first = page.locator("#lGrid .lcard select").first();
    const target = await first.evaluate((el) => {
      const opts = [...el.options].map((o) => o.value);
      return opts.find((v) => v !== el.value) || opts[0];
    });
    await first.selectOption(target);
    await page.waitForTimeout(1200);
    const after = (await (await fetch(`${BASE}/viz/api/annotations?limit=2000`)).json()).items || [];
    if (after.length <= before.length) throw new Error("改标签后没有写入标注库");
  });

  await step("点标注卡片 → 大图 + 框 + 翻页 + Esc", async () => {
    await page.click("#lGrid .lcard .lshot >> nth=0");
    await page.waitForSelector("#viewer:not([hidden])", { timeout: 8000 });
    await page.waitForFunction(
      () => (document.querySelector("#vMeta")?.textContent || "").includes("检测框"),
      { timeout: 20000 },
    );
    // 不断言"一定有框"（有的图确实没检出），而是断言"提示跟画出来的一致"
    const boxes = await page.locator("#vBoxes .bx").count();
    const metaText = await page.textContent("#vMeta");
    const claimed = Number((metaText.match(/(\d+)\s*个/) || [])[1] || 0);
    if (claimed !== boxes) {
      throw new Error(`提示说 ${claimed} 个框，实际画出 ${boxes} 个（${metaText.slice(-30)}）`);
    }
    const before = await page.textContent("#vCount");
    await page.keyboard.press("ArrowRight");
    await page.waitForTimeout(800);
    if (before === await page.textContent("#vCount")) throw new Error("按 → 没有翻页");
    await page.keyboard.press("Escape");
    await page.waitForTimeout(300);
    if (!(await page.locator("#viewer").isHidden())) throw new Error("Esc 没关掉");
    await shot(page, "04_viewer");
  });

  await step("错误样本：分组 + 行渲染", async () => {
    await page.click('.nav-item[data-view="mistakes"]');
    await page.waitForSelector("#mGroups .cls", { timeout: 8000 });
    await page.click("#mGroups .cls >> nth=0");
    await page.waitForSelector("#mGrid .lcard", { timeout: 8000 });
    await shot(page, "05_mistakes");
  });

  await step("模型版本：列表 + 当前版本高亮", async () => {
    await page.click('.nav-item[data-view="model"]');
    await page.waitForSelector("#modelTable table", { timeout: 8000 });
    const rows = await page.locator("#modelTable tbody tr").count();
    if (rows < 1) throw new Error("模型版本表为空");
    const active = await page.locator("#modelTable .pill.ok").count();
    if (active !== 1) throw new Error(`应恰好有 1 个"使用中"，实际 ${active}`);
    await shot(page, "08_model");
  });

  await step("在线识别：上传两张 → 出结论", async () => {
    await page.click('.nav-item[data-view="infer"]');
    const dirs = {
      plate: "/Users/deepway/data/spmcv/data/SPM系统AI识别训练照片_新增_20261111-20261113/01_接车照片/01_车头及车牌号",
    };
    const { readdir } = await import("node:fs/promises");
    const files = (await readdir(dirs.plate)).filter((f) => /\.(png|jpg)$/i.test(f)).slice(0, 2);
    await page.setInputFiles("#files", files.map((f) => `${dirs.plate}/${f}`));
    await page.waitForSelector("#inferImgWrap:not([hidden])", { timeout: 10000 });
    await page.click("#run");
    await page.waitForSelector("#out .card", { timeout: 90000 });
    await shot(page, "07_infer");
    // v3 起结论用 verdict 横幅表达，不再依赖 .tag
    const verdict = await page.textContent("#out .verdict .v-t");
    if (!verdict || !verdict.trim()) throw new Error("结果卡里没有结论（verdict）");
    const tools = await page.locator("#out .result-tools button").count();
    if (tools < 2) throw new Error("结果区缺少「复制 JSON / 重新识别」按钮");
  });

  await step("识别页有空态与工单字段", async () => {
    // 切分类会清空上一次结果，应该重新出现空态说明，而不是半边空白
    await page.click('#catSeg button[data-cat="repair"]');
    await page.waitForTimeout(500);
    const hasEmpty = await page.locator("#out .empty-state").count();
    if (!hasEmpty) throw new Error("识别页没有结果空态");
    const fields = await page.locator("#inferParams .pf").count();
    if (fields !== 2) throw new Error(`接车参数区应有 2 个字段，实际 ${fields}`);
  });

  await step("/metrics 是 Prometheus 文本", async () => {
    const r = await fetch(`${BASE}/metrics`);
    const type = r.headers.get("content-type") || "";
    if (!type.startsWith("text/plain")) throw new Error(`content-type=${type}`);
    const text = await r.text();
    if (!text.startsWith("# TYPE spm_image")) throw new Error("响应不是 Prometheus 文本");
  });

  await step("移动端 390px 无横向溢出 + 抽屉", async () => {
    const ctx = await browser.newContext({
      viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true,
    });
    const mp = await ctx.newPage();
    await mp.goto(`${BASE}/ui/console.html#overview`, { waitUntil: "networkidle" });
    await mp.waitForTimeout(800);
    const overflow = await mp.evaluate(() =>
      document.documentElement.scrollWidth > window.innerWidth + 1);
    if (overflow) {
      throw new Error(`移动端横向溢出 ${await mp.evaluate(() => document.documentElement.scrollWidth)}px`);
    }
    const sidebarOff = await mp.evaluate(() =>
      document.querySelector(".sidebar").getBoundingClientRect().right <= 0);
    if (!sidebarOff) throw new Error("移动端侧边栏默认应收起");
    await mp.tap("#menuBtn");
    await mp.waitForTimeout(400);
    const open = await mp.evaluate(() => document.body.classList.contains("drawer-open"));
    if (!open) throw new Error("点菜单没有打开抽屉");
    await mp.screenshot({ path: `${OUT}/09_mobile_drawer.png` });
    // 点导航项应同时切页并收起抽屉（点遮罩中心会被侧边栏挡住，不适合做自动化断言）
    await mp.tap('.nav-item[data-view="infer"]');
    await mp.waitForTimeout(500);
    const closed = await mp.evaluate(() => !document.body.classList.contains("drawer-open"));
    if (!closed) throw new Error("切页后抽屉没关");
    const inferVisible = await mp.evaluate(() => !document.querySelector("#view-infer").hidden);
    if (!inferVisible) throw new Error("移动端切页失败");
    await mp.screenshot({ path: `${OUT}/09_mobile_infer.png`, fullPage: true });
    await ctx.close();
  });

  if (errors.length) {
    console.log("\n⚠️  页面控制台报错：");
    errors.slice(0, 8).forEach((e) => console.log("   ", e.slice(0, 200)));
  }

  await browser.close();

  const failed = results.filter((r) => !r.ok);
  await writeFile(`${OUT}/report.json`, JSON.stringify({ base: BASE, results, errors }, null, 2));
  console.log(`\n通过 ${results.length - failed.length}/${results.length}　截图在 ${OUT}`);
  process.exit(failed.length || errors.length ? 1 : 0);
}

main().catch((e) => { console.error(e); process.exit(1); });
