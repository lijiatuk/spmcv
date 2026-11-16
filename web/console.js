/* SPM 控制台：总览 / 识别 / 数据集 / 评测 / 错误样本 / 训练曲线 */
(() => {
  const $ = (id) => document.getElementById(id);
  const el = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  };
  // 统一走 SSO 封装：自动注入 authorization/appCode/poolCode，401 统一登出
  const api = async (p, opt) => {
    const r = await (window.SSO ? window.SSO.authFetch : fetch)(
      p, { cache: "no-store", ...(opt || {}) }
    );
    if (!r.ok) {
      let body = {};
      try { body = await r.json(); } catch {}
      const err = new Error(body.message || `HTTP ${r.status}`);
      err.code = body.code; err.requestId = body.request_id;
      throw err;
    }
    return r.json();
  };

  const SCENE = {
    arrival_plate: "车牌", license_vin: "行驶证", frame_vin: "车架 VIN",
    odometer: "里程表", bridge_drain: "桥放油", bridge_fill: "桥加油",
    tire_drain: "轮胎放油", tire_fill: "轮胎加油",
    filter_none: "无滤芯", filter_has: "有滤芯",
    service_front: "服务单首页", service_sign: "服务单末页",
    axle_oil: "桥油液", tire_oil: "轮胎油液", filter: "滤芯",
  };
  SCENE.other = "其他";
  SCENE.uncertain = "无法判断";
  const sname = (k) => SCENE[k] || k || "—";
  const TITLES = { overview: "总览", infer: "在线识别", dataset: "数据集", eval: "精度评测",
                   mistakes: "错误样本", label: "快速标注", train: "训练曲线", model: "模型版本" };
  let allowedViews = null;   // null = 不鉴权，全部可用
  const loaded = new Set();

  /* ---------- 路由 ---------- */
  let currentView = null;

  function go(view) {
    if (!TITLES[view]) view = "overview";
    if (view === currentView) return;      // 避免 hashchange → go() → 改 hash 的递归
    currentView = view;
    if (allowedViews && !allowedViews.has(view)) { showDeny(view); return; }
    document.querySelectorAll(".nav-item").forEach((a) =>
      a.classList.toggle("on", a.dataset.view === view));
    Object.keys(TITLES).forEach((v) => { $("view-" + v).hidden = v !== view; });
    $("pageTitle").textContent = TITLES[view];
    location.hash = view;
    if (view === "overview") loadOverview();
    if (view === "eval") loadEval();
    if (view === "dataset") loadDataset();
    if (view === "mistakes") { initTargetSelect(); loadMistakes(); }
    if (view === "label") { initBatchSelect(); loadLabelQueue(); }
    if (view === "model") { initTrainPanel(); loadModelVersions(); pollTrain(); }
    if (view === "train") loadTrain();
  }
  // 浏览器前进/后退、手改地址栏 hash 都要能切页
  window.addEventListener("hashchange", () => {
    const v = (location.hash || "#overview").slice(1);
    if (v && v !== currentView) go(v);
  });

  document.querySelector(".sidebar").addEventListener("click", (e) => {
    const a = e.target.closest(".nav-item");
    if (a) go(a.dataset.view);
  });

  /* ---------- 总览 ---------- */
  let ov = null;
  let evalMode = "new";

  function kpi(icon, value, label, kind) {
    const c = el("div", "kpi" + (kind ? " " + kind : ""));
    c.append(el("div", "ico", icon));
    const box = el("div");
    box.append(el("div", "v", value), el("div", "k", label));
    c.append(box);
    return c;
  }

  function barChart(items, keyLabel, keyValue) {
    const w = 900, h = 230, padL = 40, padB = 44, padT = 10;
    const ns = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(ns, "svg");
    svg.setAttribute("class", "chart");
    svg.setAttribute("viewBox", `0 0 ${w} ${h}`);
    const defs = document.createElementNS(ns, "defs");
    defs.innerHTML = '<linearGradient id="barGrad" x1="0" y1="0" x2="0" y2="1">'
      + '<stop offset="0%" stop-color="#3b82f6"/><stop offset="100%" stop-color="#93c5fd"/></linearGradient>';
    svg.append(defs);

    for (let i = 0; i <= 4; i++) {
      const y = padT + (h - padT - padB) * (i / 4);
      const l = document.createElementNS(ns, "line");
      l.setAttribute("x1", padL); l.setAttribute("x2", w - 4);
      l.setAttribute("y1", y); l.setAttribute("y2", y);
      l.setAttribute("class", "grid-line");
      svg.append(l);
      const t = document.createElementNS(ns, "text");
      t.setAttribute("x", 4); t.setAttribute("y", y + 3);
      t.setAttribute("class", "axis");
      t.textContent = `${100 - i * 25}%`;
      svg.append(t);
    }

    const n = items.length || 1;
    const bw = Math.max(8, (w - padL - 8) / n - 6);
    items.forEach((it, i) => {
      const v = Math.max(0, Math.min(1, it[keyValue] ?? 0));
      const x = padL + i * ((w - padL - 8) / n) + 3;
      const bh = (h - padT - padB) * v;
      const r = document.createElementNS(ns, "rect");
      r.setAttribute("x", x); r.setAttribute("y", h - padB - bh);
      r.setAttribute("width", bw); r.setAttribute("height", Math.max(1, bh));
      r.setAttribute("rx", 3);
      r.setAttribute("class", "bar" + (v < 0.7 ? " low" : ""));
      svg.append(r);
      const t = document.createElementNS(ns, "text");
      t.setAttribute("x", x + bw / 2); t.setAttribute("y", h - padB + 14);
      t.setAttribute("class", "axis"); t.setAttribute("text-anchor", "middle");
      t.setAttribute("font-size", "11");
      t.textContent = it[keyLabel];
      svg.append(t);
      const v2 = document.createElementNS(ns, "text");
      v2.setAttribute("x", x + bw / 2); v2.setAttribute("y", h - padB + 29);
      v2.setAttribute("class", "axis"); v2.setAttribute("text-anchor", "middle");
      v2.setAttribute("font-size", "11");
      v2.textContent = `${Math.round(v * 100)}%`;
      svg.append(v2);
    });
    return svg;
  }

  // 形如 "01_接车照片/04_里程表"，取最后一段并去掉序号前缀
  const SHORT = {
    "01_车头及车牌号": "车牌", "02_行驶证VIN": "行驶证", "03_车架贴牌VIN": "车架",
    "04_里程表": "里程表",
    "01_桥放油": "桥放油", "02_桥加油": "桥加油",
    "03_轮胎放油": "轮胎放", "04_轮胎加油": "轮胎加",
    "05_无滤芯": "无滤芯", "06_有滤芯": "有滤芯",
    "01_服务单首页VIN": "首页", "02_服务单末页客户签字及服务站盖章": "签章",
  };
  function shortName(folder) {
    const leaf = String(folder).split("/").pop();
    if (SHORT[leaf]) return SHORT[leaf];
    return leaf.replace(/^\d+_/, "").replace(/VIN$/i, "").slice(0, 5);
  }

  async function loadOverview() {
    try {
      ov = await api("/viz/api/overview");
    } catch (err) {
      $("kpis").replaceChildren(el("div", "meta", "加载失败：" + err.message));
      return;
    }
    const perf = ov.perf.image || {};
    const oof = ov.eval.oof || {};
    const newDomain = oof.per_bucket?.new || ov.eval.new_domain || {};
    $("kpis").replaceChildren(
      kpi("▤", String(ov.dataset.total), "数据集张数"),
      kpi("◈", ov.model.version, "模型版本", "b"),
      kpi("↗", oof.top1 != null ? `${(oof.top1 * 100).toFixed(1)}%` : "—", "折外总体准确率", "b"),
      kpi("⌁", newDomain.top1 != null ? `${(newDomain.top1 * 100).toFixed(1)}%` : "—", "新域折外准确率", "c"),
    );
    $("modelVersion").textContent = `模型 ${ov.model.version} · ${ov.model.heads} 个头`;
    renderAccChart();
    renderModels(perf);
  }

  function renderAccChart() {
    const domain = ov.eval.oof?.per_bucket?.[evalMode]
      || (evalMode === "old" ? ov.eval.old_domain : ov.eval.new_domain);
    const src = domain?.per_class || {};
    const rows = Object.entries(src).map(([label, r]) => ({
      label: sname(label), acc: r.recall, support: r.support,
    }));
    if (!rows.length) {
      $("accChart").replaceChildren(el("div", "meta",
        evalMode === "old" ? "没有老域折外评测" : "没有严格折外评测结果"));
      return;
    }
    const chart = barChart(rows, "label", "acc");
    const title = document.querySelector("#view-overview .card .t");
    if (title) title.textContent = `${evalMode === "new" ? "新域" : "旧域"}折外逐类召回率（按工单留出）`;
    $("accChart").replaceChildren(chart);
  }

  (function bindEvalToggle() {
    const seg = document.querySelector(".seg[data-eval]");
    if (!seg) return;
    seg.addEventListener("click", (e) => {
      const b = e.target.closest("button");
      if (!b) return;
      [...seg.children].forEach((x) => x.classList.toggle("on", x === b));
      evalMode = b.dataset.eval;
      renderAccChart();
    });
  })();

  function renderModels(perf) {
    const list = $("modelList");
    list.replaceChildren();
    const names = { scene: "场景分类", group: "油液分组", action: "动作 6 类",
                    bin_axle: "桥 加/放", bin_tire: "轮胎 加/放", bin_filter: "滤芯 有/无", det: "检测器" };
    (ov.model.roles || []).forEach((r) => {
      const row = el("div", "mrow");
      const box = el("div", "name");
      box.append(el("div", null, names[r] || r), el("div", "sub", r));
      const isAux = ["group", "action", "bin_axle", "bin_tire", "bin_filter"].includes(r);
      const active = !isAux || ov.model.fusion_enabled;
      row.append(box, el("span", "pill " + (active ? "ok" : "warn"), active ? "启用" : "未用于判定"));
      list.append(row);
    });
    $("modelsOnline").textContent = ov.model.fusion_enabled
      ? "辅助融合开启 · 需评估"
      : "场景头判定 · 辅助头停用";
  }

  /* ---------- 识别 ---------- */
  const CATS = {
    arrival: { path: "/v1/arrival", hint: "点击或拖入接车照片", name: "接车照片" },
    repair: { path: "/v1/repair", hint: "点击或拖入故障及修复照片", name: "故障及修复" },
    inspection: { path: "/v1/inspection", hint: "点击或拖入项目检查表照片", name: "项目检查表" },
  };
  let cat = "arrival", files = [], scenes = [], urls = [], photoBoxes = [], photoFlags = [];

  $("catSeg").addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b || b.classList.contains("on")) return;
    cat = b.dataset.cat;
    [...$("catSeg").children].forEach((x) => x.classList.toggle("on", x === b));
    $("hint").textContent = CATS[cat].hint;
    files = []; scenes = []; photoBoxes = []; activeIdx = 0;
    drawPreview(); $("run").disabled = true; $("out").replaceChildren();
    $("inferImgWrap").hidden = true; $("drop").classList.remove("compact");
  });

  function drawPreview() {
    // 只负责把文件转成 objectURL 并释放旧的；缩略图条已按需求移除。
    urls.forEach((u) => URL.revokeObjectURL(u));
    urls = files.map((f) => URL.createObjectURL(f));
    if (activeIdx >= files.length) activeIdx = 0;
  }

  let activeIdx = 0;

  function showInferImage(i) {
    const wrap = $("inferImgWrap");
    if (!files.length) { wrap.hidden = true; $("drop").classList.remove("compact"); return; }
    activeIdx = Math.max(0, Math.min(i, files.length - 1));
    const img = $("inferImg");
    img.src = urls[activeIdx] || "";
    wrap.hidden = false;
    $("drop").classList.add("compact");
    $("hint").textContent = files.length > 1
      ? `已选 ${files.length} 张 · 用两侧箭头切换查看`
      : "已选 1 张 · 点「识别」开始，或再拖入更多照片";
    $("inferCounter").textContent = files.length > 1 ? `${activeIdx + 1} / ${files.length}` : "";
    $("inferPrev").hidden = files.length < 2 || activeIdx === 0;
    $("inferNext").hidden = files.length < 2 || activeIdx >= files.length - 1;
    $("inferBoxes").replaceChildren();
    const boxes = photoBoxes[activeIdx] || [];
    if (boxes.length) {
      const paint = () => paintBoxesWhenReady($("inferBoxes"), boxes, img);
      if (img.complete && img.naturalWidth) paint();
      else img.addEventListener("load", paint, { once: true });
    }
  }

  $("inferPrev").addEventListener("click", (e) => {
    e.stopPropagation(); showInferImage(activeIdx - 1);
  });
  $("inferNext").addEventListener("click", (e) => {
    e.stopPropagation(); showInferImage(activeIdx + 1);
  });

  function setFiles(list) {
    files = Array.from(list).filter((f) => f.type.startsWith("image/"));
    scenes = []; photoBoxes = []; activeIdx = 0;
    $("files").value = "";
    drawPreview(); $("run").disabled = files.length === 0;
    $("out").replaceChildren();
    showInferImage(0);
  }
  $("files").addEventListener("change", (e) => setFiles(e.target.files));
  const drop = $("drop");
  ["dragenter", "dragover"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
  drop.addEventListener("drop", (e) => setFiles(e.dataTransfer.files));

  const stat = (label, value, opts = {}) => {
    const box = el("div", "stat");
    box.append(el("div", "k", label));
    const v = el("div", "v", value == null || value === "" ? "—" : String(value));
    if (opts.small) v.classList.add("sm");
    if (opts.tone) v.classList.add(opts.tone);
    box.append(v); return box;
  };

  function renderResult(d) {
    const wrap = el("div", "card"); wrap.style.marginTop = "14px";
    const insideN = (d.photos || []).length;
    const outsideN = (d.unexpected || []).length;
    const head = el("div", "result-head");
    const name = el("div", "name",
      `${CATS[cat].name} · 本类 ${insideN} 张` + (outsideN ? ` · 其他 ${outsideN} 张` : ""));
    head.append(name, el("span", "tag " + (d.ok ? "ok" : "warn"), d.ok ? "通过" : "复核"));
    wrap.append(head);

    if (d.degraded) {
      wrap.append(el("div", "banner-warn", "部分照片分析超时，结果不完整；可重试或减少单次张数"));
    }

    const seen = [...new Set((d.photos || []).map((p) => sname(p.scene)))].filter(Boolean);
    if (seen.length) wrap.append(el("div", "seen", "识别为：" + seen.join("、")));

    const s = el("div", "stats");
    if (cat === "arrival") {
      const v = d.vin || {};
      let vinText = null, vinTone;
      if (v.license && v.frame) {
        vinText = v.license === v.frame ? "一致" : "不一致";
        vinTone = v.license === v.frame ? "ok" : "warn";
      } else if (v.license) { vinText = "仅行驶证"; vinTone = "warn"; }
      else if (v.frame) { vinText = "仅车架"; vinTone = "warn"; }
      s.append(stat("车牌", d.plate, { small: true }),
        stat("里程", d.odometer != null ? d.odometer + " km" : null),
        stat("VIN", vinText, { small: true, tone: vinTone }));

      if (v.license || v.frame || v.license_unverified || v.frame_unverified) {
        const box = el("div", "vinbox");
        const line = (k, val, bad) => {
          if (!val) return;
          const row = el("div", "vinrow");
          row.append(el("span", null, k));
          row.append(el("b", bad ? "bad" : "", val));
          box.append(row);
        };
        line("行驶证", v.license || v.license_unverified, !v.license && v.license_unverified);
        line("车架", v.frame || v.frame_unverified, !v.frame && v.frame_unverified);
        wrap.append(box);
      }
    } else if (cat === "repair") {
      const shot = (px) => (d.photos || []).some((p) => px.some((x) => (p.scene || "").startsWith(x)));
      const cell = (l, v, took) => v ? stat(l, v, { tone: "ok" })
        : stat(l, took ? "待判定" : "未拍", { small: true, tone: took ? "warn" : undefined });
      s.append(cell("桥", d.oil.axle_label, shot(["bridge_drain", "bridge_fill", "axle_oil"])),
        cell("轮胎", d.oil.tire_label, shot(["tire_drain", "tire_fill", "tire_oil"])),
        cell("滤芯", d.filter.label, shot(["filter_has", "filter_none", "filter"])));
    } else {
      s.append(stat("签字", d.signature ? "已检" : "未检", { small: true, tone: d.signature ? "ok" : "warn" }),
        stat("盖章", d.stamp ? "已检" : "未检", { small: true, tone: d.stamp ? "ok" : "warn" }),
        stat("首页 VIN", d.service_front_vin || d.service_front_vin_partial, { small: true }));
    }
    wrap.append(s);

    if (d.reasons?.length) {
      const ul = el("ul", "tips");
      d.reasons.forEach((r) => ul.append(el("li", null, r)));
      wrap.append(ul);
    }
    const rows = [...(d.photos || []), ...(d.unexpected || [])];
    if (rows.length) {
      const details = el("details", "photo-details");
      details.append(el("summary", null, `逐张结果与复核原因（${rows.length} 张）`));
      rows.forEach((p) => {
        const row = el("div", "photo-result");
        const head = el("div", "photo-result-head");
        head.append(el("b", null, p.filename || `照片 ${Number(p.index) + 1}`),
          el("span", "tag " + (p.need_review ? "warn" : "ok"), p.scene ? sname(p.scene) : "未识别"),
          el("span", "meta", p.confidence != null ? `置信度 ${(p.confidence * 100).toFixed(1)}%` : ""));
        row.append(head);
        if (p.need_review && p.review_reasons?.length) {
          p.review_reasons.forEach((reason) => row.append(el("div", "photo-result-reason", reason)));
        }
        if (p.action) row.append(el("div", "meta", `动作：${p.action}`));
        // OCR 原文：判断"没拍到"还是"拍到了没读对"的唯一依据
        if (p.ocr_text) {
          const ocr = el("details", "ocr-details");
          ocr.append(el("summary", null, "OCR 原文"));
          ocr.append(el("pre", "ocr-text", p.ocr_text));
          row.append(ocr);
        } else if (p.scene) {
          row.append(el("div", "meta", "OCR 无输出（可能没有可读文字）"));
        }
        if (p.model_version) row.append(el("div", "meta", `模型：${p.model_version}`));
        details.append(row);
      });
      wrap.append(details);
    }
    return wrap;
  }

  $("run").addEventListener("click", async () => {
    if (!files.length) return;
    $("run").disabled = true;
    const label = $("run").textContent;
    $("run").textContent = `识别中…（${files.length} 张）`;
    $("out").replaceChildren(el("div", "meta", "识别中…"));
    const fd = new FormData();
    files.forEach((f) => fd.append("images", f));
    try {
      const body = await api(CATS[cat].path + "?boxes=1", { method: "POST", body: fd });
      scenes = files.map(() => null);
      photoBoxes = files.map(() => []);
      photoFlags = files.map(() => null);
      const outside = new Set((body.unexpected || []).map((p) => p.index));
      [...(body.photos || []), ...(body.unexpected || [])].forEach((p) => {
        if (typeof p.index !== "number") return;
        scenes[p.index] = outside.has(p.index) ? "outside" : p.scene;
        photoBoxes[p.index] = p.boxes || [];
        photoFlags[p.index] = p.timed_out ? "timeout"
          : (p.scene === "unknown_scene" || !p.scene) ? "unknown"
          : outside.has(p.index) ? "outside" : null;
      });
      drawPreview();
      showInferImage(0);
      $("out").replaceChildren(renderResult(body));
      $("out").scrollIntoView({ behavior: "smooth", block: "nearest" });
    } catch (err) {
      $("out").replaceChildren(el("div", "meta", "失败：" + err.message));
    } finally { $("run").disabled = false; $("run").textContent = label; }
  });

  /* ---------- 大图查看器（一张张点开看） ---------- */
  const viewer = {
    items: [], i: 0, loadBoxes: null, boxesOn: true,

    async open(items, index, loadBoxes) {
      this.items = items; this.i = index || 0; this.loadBoxes = loadBoxes;
      $("viewer").hidden = false;
      await this.render();
    },

    close() { $("viewer").hidden = true; $("vImg").src = ""; $("vBoxes").replaceChildren(); },

    async render() {
      const it = this.items[this.i];
      if (!it) return this.close();
      $("vTitle").textContent = it.title || "";
      $("vName").textContent = it.name || "";
      $("vCount").textContent = `${this.i + 1} / ${this.items.length}`;
      $("vPrev").disabled = this.i <= 0;
      $("vNext").disabled = this.i >= this.items.length - 1;

      const img = $("vImg");
      $("vBoxes").replaceChildren();
      img.src = it.src;
      await new Promise((r) => { img.onload = r; if (img.complete && img.naturalWidth) r(); });

      const meta = el("div");
      (it.meta || []).forEach(([k, v]) => {
        const row = el("div", "kv");
        row.append(el("span", null, k), el("b", null, v == null || v === "" ? "—" : String(v)));
        meta.append(row);
      });
      $("vMeta").replaceChildren(meta);

      if (!this.boxesOn || !this.loadBoxes) return;
      const hint = el("div", "kv");
      try {
        const boxes = await this.loadBoxes(it);
        await paintBoxesWhenReady($("vBoxes"), boxes || [], img);
        hint.append(el("span", null, "检测框"),
                    el("b", null, boxes && boxes.length ? `${boxes.length} 个` : "未检测到"));
      } catch (err) {
        console.error("[viewer] 画框失败", err);
        hint.append(el("span", null, "检测框"), el("b", null, "加载失败"));
      }
      $("vMeta").append(hint);
    },

    step(d) {
      const n = this.i + d;
      if (n < 0 || n >= this.items.length) return;
      this.i = n; this.render();
    },
  };

  $("vClose").addEventListener("click", () => viewer.close());
  $("vPrev").addEventListener("click", () => viewer.step(-1));
  $("vNext").addEventListener("click", () => viewer.step(1));
  $("viewer").addEventListener("click", (e) => { if (e.target.id === "viewer") viewer.close(); });
  $("vShowBoxes").addEventListener("change", (e) => {
    viewer.boxesOn = e.target.checked; viewer.render();
  });
  document.addEventListener("keydown", (e) => {
    if ($("viewer").hidden) return;
    if (e.key === "Escape") viewer.close();
    if (e.key === "ArrowLeft") viewer.step(-1);
    if (e.key === "ArrowRight") viewer.step(1);
  });

  /* ---------- 画框 ---------- */
  // 图片刚插入时可能还没排版（rect.width=0），这时画框会静默失败。
  // 等一帧再画，最多等 10 帧。
  async function paintBoxesWhenReady(container, boxes, imgEl) {
    for (let i = 0; i < 10; i++) {
      const rect = imgEl.getBoundingClientRect();
      if (rect.width > 0 && imgEl.naturalWidth > 0) {
        paintBoxes(container, boxes, imgEl);
        return true;
      }
      await new Promise((r) => requestAnimationFrame(r));
    }
    return false;
  }

  function paintBoxes(container, boxes, imgEl) {
    container.replaceChildren();
    if (!boxes || !boxes.length) return;
    const nw = imgEl.naturalWidth, nh = imgEl.naturalHeight;
    const rect = imgEl.getBoundingClientRect();
    if (!nw || !nh || !rect.width || !rect.height) return;

    // object-fit: contain 会在元素内部留黑边，元素矩形 ≠ 图像实际绘制区域。
    // 必须按等比缩放算出真正的显示尺寸，再把居中留白加回偏移，否则框会整体偏。
    const host = container.getBoundingClientRect();
    const scale = Math.min(rect.width / nw, rect.height / nh);
    const dispW = nw * scale;
    const dispH = nh * scale;
    const ox = (rect.left - host.left) + (rect.width - dispW) / 2;
    const oy = (rect.top - host.top) + (rect.height - dispH) / 2;

    boxes.forEach((b) => {
      const [x1, y1, x2, y2] = b.box;
      const d = el("div", "bx");
      d.style.left = `${ox + x1 * scale}px`;
      d.style.top = `${oy + y1 * scale}px`;
      d.style.width = `${(x2 - x1) * scale}px`;
      d.style.height = `${(y2 - y1) * scale}px`;
      const tag = el("span", null, `${b.label} ${b.score.toFixed(2)}`);
      // 框贴着上边缘时标签会被裁掉，改画在框内侧
      if (oy + y1 * scale < 18) tag.style.top = "1px";
      d.append(tag);
      container.append(d);
    });
  }

  /* ---------- 数据集：上传 + 自动标注 ---------- */
  async function loadDataset() {
    const box = $("dsBatches");
    const cnt = $("dsCount");
    try {
      const d = await api("/viz/api/dataset/batches");
      const list = d.batches || [];
      cnt.textContent = `共 ${list.length} 批`;
      box.replaceChildren();
      if (!list.length) {
        box.append(el("div", "meta", "还没有上传过数据集。把按类别建好目录的 zip 拖到上面。"));
        return;
      }
      list.forEach((b) => {
        const card = el("div", "ds-batch");
        const top = el("div", "top");
        top.append(el("span", "name", b.batch),
                   el("span", "when", (b.created_at || "").replace("T", " ").slice(0, 16)),
                   el("span", "when", `${b.images} 张`));
        const acts = el("div", "acts");
        const stat = el("span", "batch-stat", "");
        const btn = el("button", "ghost", "自动标注");
        btn.addEventListener("click", async () => {
          btn.disabled = true; btn.textContent = "标注中…";
          try {
            const r = await api(`/viz/api/dataset/${encodeURIComponent(b.batch)}/autolabel?limit=500`,
              { method: "POST" });
            stat.textContent = `已标 ${r.saved} 张，平均置信度 ${r.avg_confidence ?? "—"}，` +
              `高置信 ${r.high_confidence}`;
            btn.textContent = "重新标注";
          } catch (err) {
            stat.textContent = "失败：" + err.message;
            btn.textContent = "自动标注";
          } finally { btn.disabled = false; }
        });
        acts.append(stat, btn);
        top.append(acts);

        const dist = el("div", "dist");
        Object.entries(b.classes || {}).forEach(([k, v]) => {
          dist.append(el("span", "chip", `${sname(k)} ${v}`));
        });
        if (b.unmapped_top_dirs && Object.keys(b.unmapped_top_dirs).length) {
          const bad = Object.entries(b.unmapped_top_dirs).map(([k, v]) => `${k}(${v})`).join("、");
          dist.append(el("span", "chip", `未归类目录：${bad}`));
        }
        card.append(top, dist);
        box.append(card);
      });
    } catch (err) {
      box.replaceChildren(el("div", "meta", "加载失败：" + err.message));
    }
  }

  async function uploadDataset(file) {
    const out = $("dsResult");
    out.replaceChildren(el("div", "meta", "上传中…"));
    const fd = new FormData();
    fd.append("file", file);
    try {
      const r = await fetch("/viz/api/dataset/upload", { method: "POST", body: fd });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || `HTTP ${r.status}`);
      const dist = Object.entries(d.classes || {}).map(([k, v]) => `${sname(k)} ${v}`).join("、");
      out.replaceChildren(el("div", "meta",
        `已导入 ${d.images} 张（${(d.bytes / 1024 / 1024).toFixed(1)}MB）：${dist}`));
      loadDataset();
    } catch (err) {
      out.replaceChildren(el("div", "meta", "上传失败：" + err.message));
    }
  }

  $("dsFile").addEventListener("change", (e) => {
    if (e.target.files[0]) uploadDataset(e.target.files[0]);
  });
  const dsDrop = $("dsDrop");
  ["dragenter", "dragover"].forEach((t) => dsDrop.addEventListener(t, (e) => {
    e.preventDefault(); dsDrop.classList.add("over");
  }));
  ["dragleave", "drop"].forEach((t) => dsDrop.addEventListener(t, (e) => {
    e.preventDefault(); dsDrop.classList.remove("over");
  }));
  dsDrop.addEventListener("drop", (e) => {
    const f = e.dataTransfer.files[0];
    if (f) uploadDataset(f);
  });

  /* ---------- 评测 ---------- */
  async function loadEval() {
    const box = $("evalCard");
    if (loaded.has("eval")) return;
    loaded.add("eval");
    const d = await api("/viz/api/eval");
    box.replaceChildren();
    const h = el("div", "card-head");
    h.append(el("span", "t", "严格折外评测（工单分组）"));
    const seg = el("div", "seg");
    ["new", "old"].forEach((domain) => {
      const b = el("button", domain === evalMode ? "on" : "", domain === "new" ? "新域" : "旧域");
      b.addEventListener("click", () => { evalMode = domain; renderOof(); });
      seg.append(b);
    });
    h.append(seg);
    box.append(h);
    const note = el("div", "meta", `严格折外：${d.oof?.n || 0} 张；每张仅由未见过该工单的模型预测。目录回放结果含训练样本，不用于验收。`);
    box.append(note);
    const t = el("table", "tbl");
    box.append(t);
    const folds = el("div", "seen", `总体 Top-1 ${(d.oof?.top1 * 100).toFixed(2)}% · Top-5 ${(d.oof?.top5 * 100).toFixed(2)}% · 高置信覆盖 ${(d.oof?.auto_coverage * 100).toFixed(1)}% · 覆盖内场景准确率 ${(d.oof?.auto_precision * 100).toFixed(1)}%`);
    box.append(folds);
    const replay = el("details");
    replay.style.marginTop = "14px";
    const summary = el("summary", null, "查看目录回放参考（含训练样本，不是泛化指标）");
    replay.append(summary);
    const replayTable = el("table", "tbl");
    const replayHead = el("tr"); ["目录", "张数", "回放准确率", "主要误判"].forEach((x) => replayHead.append(el("th", null, x)));
    const replayBody = el("tbody");
    (d.folders_new?.by_folder || []).forEach((r) => {
      const tr = el("tr");
      tr.append(el("td", null, r.folder), el("td", "num", String(r.n)),
        el("td", "num", `${(r.accuracy * 100).toFixed(1)}%`),
        el("td", "num", (r.confusions || []).slice(0, 2).map(([k, v]) => `${sname(k)}×${v}`).join("、") || "—"));
      replayBody.append(tr);
    });
    replayTable.append(replayHead, replayBody); replay.append(replayTable); box.append(replay);
    function renderOof() {
      [...seg.children].forEach((b) => b.classList.toggle("on", b.textContent === (evalMode === "new" ? "新域" : "旧域")));
      const domain = d.oof?.per_bucket?.[evalMode];
      const rows = Object.entries(domain?.per_class || {});
      const hr2 = el("tr"); ["场景", "支持数", "Precision", "Recall", "F1"].forEach((x) => hr2.append(el("th", null, x)));
      const th = el("thead"); th.append(hr2);
      const body = el("tbody");
      rows.forEach(([label, r]) => {
        const tr = el("tr");
        tr.append(el("td", null, sname(label)), el("td", "num", String(r.support)),
          el("td", "num", `${(r.precision * 100).toFixed(1)}%`),
          el("td", "num", `${(r.recall * 100).toFixed(1)}%`),
          el("td", "num", `${(r.f1 * 100).toFixed(1)}%`));
        body.append(tr);
      });
      t.replaceChildren(th, body);
      const domainSummary = el("div", "seen", `${evalMode === "new" ? "新域" : "旧域"}：${domain?.correct || 0}/${domain?.n || 0} = ${domain?.top1 != null ? (domain.top1 * 100).toFixed(2) + "%" : "暂无"}`);
      const prior = box.querySelector(".domain-summary"); if (prior) prior.remove();
      domainSummary.classList.add("domain-summary");
      folds.before(domainSummary);
    }
    renderOof();
  }

  /* ---------- 错误样本 ---------- */
  let mData = null, mGroup = null, selected = new Set(), mSource = "all";

  const ALL_CLASSES = [
    "arrival_plate", "license_vin", "frame_vin", "odometer",
    "bridge_drain", "bridge_fill", "tire_drain", "tire_fill",
    "filter_none", "filter_has", "service_front", "service_sign",
  ];

  function initTargetSelect() {
    const sel = $("mTarget");
    sel.replaceChildren();
    ALL_CLASSES.forEach((c) => {
      const o = el("option", null, sname(c));
      o.value = c;
      sel.append(o);
    });
  }

  async function loadMistakes() {
    $("mGrid").replaceChildren(el("div", "meta", "加载中…"));
    try { mData = await api(`/viz/api/mistakes?source=${mSource}`); }
    catch (err) { $("mGrid").replaceChildren(el("div", "meta", "加载失败：" + err.message)); return; }
    const box = $("mGroups"); box.replaceChildren();
    (mData.groups || []).forEach((g) => {
      const row = el("div", "cls");
      row.append(el("span", null, `${sname(g.want)} → ${sname(g.got)}`), el("span", null, String(g.n)));
      row.addEventListener("click", () => {
        [...box.children].forEach((x) => x.classList.toggle("on", x === row));
        openGroup(g);
      });
      box.append(row);
    });
    $("mGrid").replaceChildren();
    $("mSave").disabled = true;
    const first = (mData.groups || [])[0];
    if (first) {
      const firstRow = box.children[0];
      if (firstRow) firstRow.classList.add("on");
      $("mTitle").textContent = `共 ${mData.total} 张误判`;
      openGroup(first);          // 默认展开最大的一组，避免右侧空白
    } else {
      $("mTitle").textContent = "没有误判样本";
    }
  }

  function openGroup(g) {
    mGroup = g; selected = new Set();
    $("mSave").disabled = true; $("mRelabel").disabled = true;
    $("mTarget").value = g.want;
    $("mTitle").textContent = `${sname(g.want)} → ${sname(g.got)} · ${g.n} 张 · 平均置信度 ${g.avg_conf}`;

    const grid = $("mGrid");
    grid.replaceChildren();
    grid.className = "lgrid";          // 复用标注页的卡片网格，避免窄列竖排文字

    g.items.forEach((it, idx) => {
      const card = el("div", "lcard");

      const shot = el("div", "lshot");
      const img = el("img");
      img.loading = "lazy";
      img.src = `/viz/api/file?path=${encodeURIComponent(it.path)}`;
      const overlay = el("div", "boxes");
      shot.append(img, overlay);
      shot.addEventListener("click", () => {
        detectOf(it.path).then((boxes) => paintBoxesWhenReady(overlay, boxes || [], img)).catch(() => {});
        viewer.open(
          g.items.map((x) => ({
            src: `/viz/api/file?path=${encodeURIComponent(x.path)}`,
            title: `应为 ${sname(x.want)}`,
            name: x.file,
            meta: [["判定", sname(x.got)], ["置信度", `${((x.conf ?? 0) * 100).toFixed(0)}%`],
                   ["目录", x.folder]],
            path: x.path,
          })),
          idx,
          (x) => detectOf(x.path),
        );
      });

      const body = el("div", "lbody");
      body.append(el("div", "lname", it.file));

      const row = el("div", "lrow");
      const chk = el("input", "lpick");
      chk.type = "checkbox";
      chk.addEventListener("change", () => {
        if (chk.checked) selected.add(it.path); else selected.delete(it.path);
        card.classList.toggle("sel", chk.checked);
        $("mSave").disabled = selected.size === 0;
        $("mRelabel").disabled = selected.size === 0;
      });
      row.append(chk,
        el("span", "lpred bad", `判成 ${sname(it.got)}`),
        el("span", "lpred", `${((it.conf ?? 0) * 100).toFixed(0)}%`));
      body.append(row);
      body.append(el("div", "lname", `应为 ${sname(it.want)}`));

      card.append(shot, body);
      grid.append(card);
    });
  }

  $("mSelectAll").addEventListener("click", () => {
    if (!mGroup) return;
    const all = selected.size === mGroup.items.length;
    selected = all ? new Set() : new Set(mGroup.items.map((i) => i.path));
    [...$("mGrid").children].forEach((c) => {
      const chk = c.querySelector("input[type=checkbox]");
      if (chk) chk.checked = !all;
      c.style.borderColor = all ? "" : "var(--accent)";
    });
    $("mSave").disabled = selected.size === 0;
    $("mRelabel").disabled = selected.size === 0;
  });

  $("mRelabel").addEventListener("click", async () => {
    if (!selected.size || !mGroup) return;
    const to = $("mTarget").value;
    const items = mGroup.items.filter((i) => selected.has(i.path))
      .map((i) => ({ src: i.path, from: i.want, to }));
    if (!confirm(`把选中的 ${items.length} 张改为「${sname(to)}」？\n\n只会写入改标计划，不会立刻搬文件。`)) return;
    try {
      const d = await api("/viz/api/relabel_plan", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ items }),
      });
      $("mRelabel").textContent = `已加入 ${d.added} 张`;
      setTimeout(() => { $("mRelabel").textContent = "加入改标计划"; }, 1800);
    } catch (err) { $("mRelabel").textContent = "失败"; }
  });

  $("mSave").addEventListener("click", async () => {
    if (!selected.size || !mGroup) return;
    const items = mGroup.items.filter((i) => selected.has(i.path))
      .map((i) => ({ path: i.path, want: i.want, got: i.got }));
    try {
      const d = await api("/viz/api/hard_cases", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ items }),
      });
      $("mSave").textContent = `已登记 ${d.added} 张`;
      setTimeout(() => { $("mSave").textContent = "登记为难例"; }, 1600);
    } catch (err) { $("mSave").textContent = "失败"; }
  });

  $("mSource").addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    [...$("mSource").children].forEach((x) => x.classList.toggle("on", x === b));
    mSource = b.dataset.src; loadMistakes();
  });

  /* ---------- 检测框缓存（标注网格和大图共用） ---------- */
  const boxCache = new Map();
  async function detectOf(path) {
    if (!path) return [];
    if (!boxCache.has(path)) {
      try {
        const d = await api(`/viz/api/detect?path=${encodeURIComponent(path)}`);
        boxCache.set(path, d.boxes || []);
      } catch {
        boxCache.set(path, []);
      }
    }
    return boxCache.get(path);
  }

  /* ---------- 标注网格 ---------- */
  const LABEL_CLASSES = [
    "arrival_plate", "license_vin", "frame_vin", "odometer",
    "bridge_drain", "bridge_fill", "tire_drain", "tire_fill",
    "filter_none", "filter_has", "service_front", "service_sign", "other",
  ];
  let lbItems = [], lbShown = 0, lbFam = "oil", lbSelected = new Set();
  const LB_PAGE = 24;

  function labelSelect(current) {
    const sel = document.createElement("select");
    LABEL_CLASSES.forEach((c) => {
      const o = document.createElement("option");
      o.value = c; o.textContent = sname(c);
      if (c === current) o.selected = true;
      sel.append(o);
    });
    return sel;
  }

  function initBatchSelect() {
    const sel = $("lBatchLabel");
    sel.replaceChildren();
    LABEL_CLASSES.forEach((c) => {
      const o = document.createElement("option");
      o.value = c; o.textContent = sname(c);
      sel.append(o);
    });
  }

  async function loadLabelQueue() {
    lbFam = $("lFam").querySelector(".on")?.dataset.fam || "oil";
    $("lProgress").textContent = "加载中…";
    const d = await api(`/viz/api/review_queue?source=all&family=${lbFam}&limit=600`);
    lbItems = d.items || [];
    lbShown = 0; lbSelected = new Set();
    updateBatchBar();
    $("lGrid").replaceChildren();
    $("lProgress").textContent = `共 ${lbItems.length} 张待复核`;
    if (!lbItems.length) {
      $("lGrid").append(el("div", "meta", "这个范围没有待复核的图片"));
      return;
    }
    appendLabelCards();
  }

  function appendLabelCards() {
    const q = ($("lSearch").value || "").trim().toLowerCase();
    const list = q ? lbItems.filter((i) => i.file.toLowerCase().includes(q)) : lbItems;
    const slice = list.slice(lbShown, lbShown + LB_PAGE);
    slice.forEach((it) => $("lGrid").append(labelCard(it)));

    // 滑动到底再加载，避免一次渲染几百张
    const old = $("lMore");
    old.replaceChildren();
    if (lbShown + LB_PAGE < list.length) {
      const btn = el("button", null, `继续加载（还有 ${list.length - lbShown - LB_PAGE} 张）`);
      btn.addEventListener("click", () => { lbShown += LB_PAGE; appendLabelCards(); });
      old.append(btn);
    }
    lbShown += slice.length;
  }

  let lbObserver = null;
  function labelCard(it) {
    const box = el("div", "lcard");
    box.dataset.path = it.path;

    const shot = el("div", "lshot");
    const img = el("img");
    img.loading = "lazy";
    img.src = `/viz/api/file?path=${encodeURIComponent(it.path)}`;
    const overlay = el("div", "boxes");
    shot.append(img, overlay);
    shot.addEventListener("click", () => {
      viewer.open(
        lbItems.map((x) => ({
          src: `/viz/api/file?path=${encodeURIComponent(x.path)}`,
          title: `应为 ${sname(x.labeled)}`, name: x.file,
          meta: [["模型预判", sname(x.pred)], ["置信度", `${((x.conf ?? 0) * 100).toFixed(0)}%`]],
          path: x.path,
        })),
        lbItems.indexOf(it),
        (x) => detectOf(x.path),
      );
    });

    // 进入视口才画框，避免一次打几百个请求
    lbObserver = lbObserver || new IntersectionObserver((entries) => {
      entries.forEach((en) => {
        if (!en.isIntersecting) return;
        const card = en.target;
        lbObserver.unobserve(card);
        const p = card.dataset.path;
        const o = card.querySelector(".boxes");
        const im = card.querySelector("img");
        detectOf(p).then((boxes) => {
          if (im.complete && im.naturalWidth) paintBoxesWhenReady(o, boxes || [], im);
          else im.addEventListener("load", () => paintBoxesWhenReady(o, boxes || [], im), { once: true });
        }).catch(() => {});
      });
    }, { rootMargin: "200px" });
    lbObserver.observe(box);

    const body = el("div", "lbody");
    body.append(el("div", "lname", it.file));

    const row = el("div", "lrow");
    const chk = el("input", "lpick");
    chk.type = "checkbox";
    chk.addEventListener("change", () => {
      if (chk.checked) lbSelected.add(it.path); else lbSelected.delete(it.path);
      box.classList.toggle("sel", chk.checked);
      updateBatchBar();
    });
    const pred = el("span", "lpred" + (it.pred !== it.labeled ? " bad" : ""),
      `预判 ${sname(it.pred)} ${((it.conf ?? 0) * 100).toFixed(0)}%`);
    row.append(chk, pred);
    body.append(row);

    const edit = el("div", "lrow");
    edit.append(el("span", "lhint", "原标签"));
    const sel = labelSelect(it.labeled);
    // 改了下拉立即写库，不用再点确认
    sel.addEventListener("change", () => saveOne(it, sel.value, box));
    edit.append(sel);
    body.append(edit);

    box.append(shot, body);
    return box;
  }

  function updateBatchBar() {
    $("lBatch").hidden = lbSelected.size === 0;
    $("lBatchCount").textContent = `已选 ${lbSelected.size} 张`;
  }

  async function saveOne(it, label, cardEl) {
    try {
      await api("/viz/api/annotations", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ items: [{
          path: it.path, label,
          source_label: it.labeled, prediction: it.pred || "",
          confidence: it.conf, evidence_status: "visible", action_evidence: "unclear",
        }] }),
      });
      if (cardEl) cardEl.style.opacity = ".55";
    } catch (err) {
      alert("保存失败：" + err.message);
    }
  }

  async function applyBatch() {
    const label = $("lBatchLabel").value;
    const targets = lbItems.filter((i) => lbSelected.has(i.path));
    if (!targets.length) return;
    try {
      await api("/viz/api/annotations", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ items: targets.map((it) => ({
          path: it.path, label,
          source_label: it.labeled, prediction: it.pred || "",
          confidence: it.conf, evidence_status: "visible", action_evidence: "unclear",
        })) }),
      });
      [...$("lGrid").children].forEach((c) => {
        if (lbSelected.has(c.dataset.path)) c.style.opacity = ".55";
      });
      lbSelected = new Set();
      updateBatchBar();
    } catch (err) {
      alert("批量保存失败：" + err.message);
    }
  }

  $("lFam").addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    [...$("lFam").children].forEach((x) => x.classList.toggle("on", x === b));
    initBatchSelect();
    loadLabelQueue();
  });
  $("lSearch").addEventListener("input", () => {
    lbShown = 0;
    $("lGrid").replaceChildren();
    $("lMore").replaceChildren();
    appendLabelCards();
  });
  $("lApply").addEventListener("click", applyBatch);
  $("lClear").addEventListener("click", () => {
    lbSelected = new Set();
    [...$("lGrid").children].forEach((c) => {
      c.classList.remove("sel");
      const k = c.querySelector(".lpick"); if (k) k.checked = false;
    });
    updateBatchBar();
  });

  /* ---------- 训练 ---------- */
  let trainTimer = null;

  async function initTrainPanel() {
    const sel = $("trData");
    if (sel.options.length) return;
    sel.append(new Option("合并数据集 fold0（推荐）", "union:0"));
    try {
      const d = await api("/viz/api/dataset/batches");
      (d.batches || []).forEach((b) => {
        sel.append(new Option(`上传批次 ${b.batch}（${b.images} 张）`, `upload:${b.batch}`));
      });
    } catch {}
  }

  function renderTrain(j) {
    const box = $("trStatus");
    if (!j) { box.replaceChildren(el("div", "meta", "还没有训练任务")); return; }
    const pct = j.epochs ? Math.min(100, Math.round((j.epoch / j.epochs) * 100)) : 0;
    const tone = j.status === "completed" ? "ok" : j.status === "failed" ? "warn" : "";
    const head = el("div", null,
      `状态：${j.status}　epoch ${j.epoch}/${j.epochs}` +
      (j.top1 != null ? `　top1 ${(j.top1 * 100).toFixed(1)}%` : ""));
    head.className = "meta " + tone;
    const bar = el("div", "trainbar");
    const fill = el("i"); fill.style.width = pct + "%"; bar.append(fill);
    box.replaceChildren(head, bar);
    if (j.error) box.append(el("div", "banner-warn", j.error));
  }

  async function pollTrain() {
    try {
      const d = await api("/viz/api/train/status");
      if (!d.job) { renderTrain(null); return; }
      renderTrain(d.job);
      if (d.log_tail) {
        const log = el("div", "trainlog", d.log_tail);
        $("trStatus").append(log);
      }
      const running = d.running;
      $("trStart").disabled = running;
      $("trCancel").hidden = !running;
      if (running) {
        trainTimer = setTimeout(pollTrain, 2500);
      } else if (d.job.status === "completed" && d.job.weights) {
        const btn = el("button", "btn", "注册为候选版本");
        btn.style.marginTop = "8px";
        btn.addEventListener("click", async () => {
          const ver = prompt("版本号（vX.Y.Z）", "v5.2.0");
          if (!ver) return;
          try {
            await api("/viz/api/model/register", {
              method: "POST", headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ version: ver, weights: d.job.weights,
                                     metrics: { scene: { top1: d.job.top1 } } }),
            });
            $("modelHint").textContent = `已注册 ${ver}（候选，可切换）`;
            loadModelVersions();
          } catch (err) { alert("注册失败：" + err.message); }
        });
        $("trStatus").append(btn);
      }
    } catch (err) {
      renderTrain(null);
    }
  }

  $("trStart").addEventListener("click", async () => {
    const [kind, arg] = $("trData").value.split(":");
    const body = {
      dataset: kind === "union" ? "union" : `upload:${arg}`,
      fold: kind === "union" ? Number(arg || 0) : 0,
      epochs: Number($("trEpochs").value || 25),
      imgsz: Number($("trImgsz").value || 512),
    };
    $("trStart").disabled = true;
    try {
      await api("/viz/api/train/start", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      clearTimeout(trainTimer);
      pollTrain();
    } catch (err) {
      $("trStart").disabled = false;
      alert("启动失败：" + err.message);
    }
  });

  $("trCancel").addEventListener("click", async () => {
    const d = await api("/viz/api/train/status").catch(() => null);
    if (!d?.job) return;
    await api("/viz/api/train/cancel", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ job_id: d.job.job_id }),
    }).catch(() => {});
    clearTimeout(trainTimer);
    pollTrain();
  });

  /* ---------- 模型版本 ---------- */
  async function loadModelVersions() {
    const box = $("modelTable");
    box.replaceChildren(el("div", "meta", "加载中…"));
    let d;
    try {
      d = await api("/viz/api/model/versions");
    } catch (err) {
      box.replaceChildren(el("div", "meta", "加载失败：" + err.message));
      return;
    }
    const t = el("table", "tbl");
    const hr = el("tr");
    ["版本", "状态", "场景 top1", "评测口径", "创建时间", ""].forEach((x) =>
      hr.append(el("th", null, x)));
    const thead = el("thead"); thead.append(hr); t.append(thead);
    const tb = el("tbody");
    (d.versions || []).forEach((v) => {
      const tr = el("tr");
      const name = el("td", null, v.version);
      const st = el("td");
      st.append(el("span", "pill " + (v.active ? "ok" : ""), v.active ? "使用中" : (v.status || "—")));
      const top1 = v.metrics?.scene?.top1;
      const m = el("td", "num", top1 != null ? `${(top1 * 100).toFixed(2)}%` : "—");
      const sc = v.metrics?.scene || {};
      const scope = sc.note || (sc.val_n ? `单折 ${sc.val_n} 张` : "—");
      const scopeTd = el("td", "num", scope);
      scopeTd.title = "不同版本的评测集不同，数字不可直接横向比较";
      const when = el("td", "num", (v.created_at || "").replace("T", " ").slice(0, 16));
      const act = el("td");
      if (!v.active && v.exists) {
        const btn = el("button", "ghost", "切换");
        btn.addEventListener("click", async () => {
          if (!confirm(`把线上模型切到 ${v.version}？\n\n切换会立即热加载，无需重启。`)) return;
          btn.disabled = true;
          try {
            const r2 = await api("/viz/api/model/activate", {
              method: "POST", headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ version: v.version }),
            }).catch(() => null);
            $("modelHint").textContent = r2?.reloaded
              ? `已切到 ${v.version}，已生效`
              : `已切到 ${v.version}，需重启 vision 服务`;
            loadModelVersions();
          } catch (err) {
            alert("切换失败：" + err.message);
            btn.disabled = false;
          }
        });
        act.append(btn);
      }
      if (!v.active) {
        const del = el("button", "ghost", "删除");
        del.style.marginLeft = "6px";
        del.addEventListener("click", async () => {
          if (!confirm(`删除模型 ${v.version}？会同时删掉磁盘上的权重，不可恢复。`)) return;
          try {
            await api("/viz/api/model/delete", {
              method: "POST", headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ version: v.version }),
            });
            $("modelHint").textContent = `已删除 ${v.version}`;
            loadModelVersions();
          } catch (err) { alert("删除失败：" + err.message); }
        });
        act.append(del);
      }
      tr.append(name, st, m, scopeTd, when, act);
      tb.append(tr);
    });
    t.append(tb);
    box.replaceChildren(t);
  }

  /* ---------- 训练 ---------- */
  async function loadTrain() {
    if (loaded.has("train")) return;
    loaded.add("train");
    const box = $("trainCard");
    const d = await api("/viz/api/runs");
    box.replaceChildren(el("div", "card-head"));
    box.firstChild.append(el("span", "t", `训练记录（${d.runs.length} 条）`));
    if (!d.runs.length) { box.append(el("div", "meta", "var/runs 下没有训练记录")); return; }

    const t = el("table", "tbl");
    const hr = el("tr");
    ["实验", "epoch", "最高 top1", "末值 top1", ""].forEach((x) => hr.append(el("th", null, x)));
    const thead = el("thead"); thead.append(hr); t.append(thead);
    const tb = el("tbody");
    d.runs.sort((a, b) => (b.series.at(-1)?.top1 || 0) - (a.series.at(-1)?.top1 || 0)).forEach((r) => {
      const vals = r.series.map((s) => s.top1).filter((v) => v != null);
      if (!vals.length) return;
      const best = Math.max(...vals), last = vals.at(-1);
      const tr = el("tr");
      tr.append(el("td", null, r.name), el("td", "num", String(r.series.length)),
        el("td", "num", `${(best * 100).toFixed(2)}%`), el("td", "num", `${(last * 100).toFixed(2)}%`));
      const bar = el("td"); const c = el("div", "barcell"); const i = el("i");
      i.style.width = `${last * 100}%`; if (last < 0.85) i.classList.add("low");
      c.append(i); bar.append(c); tr.append(bar);
      tb.append(tr);
    });
    t.append(tb); box.append(t);
  }

  /* ---------- 服务状态 ---------- */
  async function ping() {
    try {
      const ok = (await api("/readyz")).ok;
      $("svcDot").className = "dot " + (ok ? "ok" : "bad");
      $("svcText").textContent = ok ? "服务正常" : "下游异常";
    } catch { $("svcDot").className = "dot bad"; $("svcText").textContent = "不可达"; }
  }

  function showDeny(view) {
    Object.keys(TITLES).forEach((v) => { $("view-" + v).hidden = v !== view; });
    $("pageTitle").textContent = TITLES[view] || "";
    const box = $("view-" + view);
    box.replaceChildren();
    const c = el("div", "card");
    c.style.cssText = "max-width:520px;margin:40px auto;text-align:center;padding:36px";
    c.append(el("div", null, "无权限访问"));
    const sub = el("div", "meta", `当前账号未被授权「${TITLES[view]}」，请联系管理员分配资源 ${view}。`);
    c.append(sub);
    box.append(c);
  }

  function applyMenus(me) {
    if (!me || !me.enabled) return;
    const codes = new Set(me.menus || []);
    const views = me.views || {};
    allowedViews = new Set(
      Object.keys(TITLES).filter((v) => codes.has(views[v] || v))
    );
    document.querySelectorAll(".nav-item").forEach((a) => {
      a.hidden = !allowedViews.has(a.dataset.view);
    });
    const who = me.user?.userName || me.user?.account;
    if (who) $("svcText").textContent = who;
  }

  // 侧边栏的模型信息属于全局，不能只在总览页更新——直接进识别页会一直显示"加载中"。
  function loadModelFooter() {
    api("/v1/meta").then((m) => {
      const mm = m.model || {};
      const v = mm.runtime_version || mm.configured_version;
      const roles = (mm.configured_roles || []).length;
      if (v) $("modelVersion").textContent = `模型 ${v}` + (roles ? ` · ${roles} 个头` : "");
    }).catch(() => {
      $("modelVersion").textContent = "模型信息不可用";
    });
  }

  (async function boot() {
    let me = null;
    try {
      me = await (window.SSO ? window.SSO.boot() : Promise.resolve(null));
    } catch (err) {
      if (err && err.deny) {
        $("svcText").textContent = "无权限";
        return;
      }
    }
    applyMenus(me);
    loadModelFooter();
    let start = (location.hash || "#overview").slice(1);
    if (allowedViews && !allowedViews.has(start)) {
      start = [...allowedViews][0] || "overview";
    }
    go(start);
    ping();
    setInterval(ping, 30000);
  })();
})();
