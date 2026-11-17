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

  /* ---------- 通用 UI：轻提示 / 骨架 / 空态 / 坏图占位 ---------- */
  let toastTimer = null;
  function toast(message, kind) {
    const node = $("toast");
    node.textContent = message;
    node.className = "toast on" + (kind === "bad" ? " bad" : "");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { node.className = "toast"; }, 3200);
  }

  function skeleton(rows = 3, height = 16) {
    const box = el("div", "sk");
    box.style.display = "grid";
    box.style.gap = "10px";
    for (let i = 0; i < rows; i++) {
      const bar = el("div", "skeleton");
      bar.style.height = `${height}px`;
      bar.style.width = `${i === rows - 1 ? 62 : 100}%`;
      box.append(bar);
    }
    return box;
  }

  function emptyState(icon, title, sub, actionLabel, onAction) {
    const box = el("div", "empty-state");
    box.append(el("div", "es-ico", icon), el("div", "es-t", title), el("div", "es-s", sub));
    if (actionLabel) {
      const btn = el("button", "btn es-a", actionLabel);
      btn.type = "button";
      if (onAction) btn.addEventListener("click", onAction);
      box.append(btn);
    }
    return box;
  }

  // 图片挂了（路径失效 / 文件被删）不要留一块 300px 的黑，
  // 也不要让浏览器把卡片的排版撑坏：统一换成占位。
  let brokenImages = 0;
  const brokenListeners = new Set();
  function markBroken(img) {
    if (!img || img.dataset.broken) return;
    img.dataset.broken = "1";
    brokenImages += 1;
    const host = img.parentElement;
    if (host) {
      host.classList.add("is-broken");
      if (!host.querySelector(".ph-broken")) {
        const ph = el("div", "ph-broken");
        ph.append(el("b", null, "图片不可用"), el("span", null, "文件缺失或路径失效"));
        host.append(ph);
      }
      const card = host.closest(".lcard, .card-row, .mcard");
      if (card) card.classList.add("broken");
    }
    brokenListeners.forEach((fn) => { try { fn(brokenImages); } catch (_) {} });
  }
  function resetBroken(img) {
    if (!img) return;
    delete img.dataset.broken;
    const host = img.parentElement;
    if (!host) return;
    host.classList.remove("is-broken");
    const ph = host.querySelector(".ph-broken");
    if (ph) ph.remove();
  }
  document.addEventListener("error", (e) => {
    const t = e.target;
    if (t && t.tagName === "IMG") markBroken(t);
  }, true);

  function copyText(text, okMsg) {
    const done = () => toast(okMsg || "已复制到剪贴板");
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(text).then(done).catch(() => fallback());
    } else fallback();
    function fallback() {
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.append(ta);
      ta.select();
      try { document.execCommand("copy"); done(); } catch (_) { toast("复制失败，请手动选择", "bad"); }
      ta.remove();
    }
  }

  /* ---------- 移动端导航抽屉 ---------- */
  function setDrawer(open) {
    document.body.classList.toggle("drawer-open", open);
    $("menuBtn").setAttribute("aria-expanded", String(open));
    $("menuBtn").setAttribute("aria-label", open ? "关闭导航菜单" : "打开导航菜单");
    $("scrim").hidden = !open;
  }
  $("menuBtn").addEventListener("click", () =>
    setDrawer(!document.body.classList.contains("drawer-open")));
  $("scrim").addEventListener("click", () => setDrawer(false));
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && document.body.classList.contains("drawer-open")) setDrawer(false);
  });
  window.addEventListener("resize", () => {
    if (!window.matchMedia("(max-width: 960px)").matches) setDrawer(false);
  });

  /* ---------- 路由 ---------- */
  let currentView = null;

  function go(view) {
    if (!TITLES[view]) view = "overview";
    if (view === currentView) return;      // 避免 hashchange → go() → 改 hash 的递归
    currentView = view;
    if (allowedViews && !allowedViews.has(view)) { showDeny(view); return; }
    setDrawer(false);
    document.querySelectorAll(".nav-item").forEach((a) => {
      const on = a.dataset.view === view;
      a.classList.toggle("on", on);
      if (on) a.setAttribute("aria-current", "page");
      else a.removeAttribute("aria-current");
    });
    Object.keys(TITLES).forEach((v) => { $("view-" + v).hidden = v !== view; });
    $("pageTitle").textContent = TITLES[view];
    document.title = `${TITLES[view]} · SPM 视觉识别`;
    location.hash = view;
    if (view === "infer" && !$("out").children.length) renderInferEmpty();
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
    // 跳过页内锚点（例如“跳到主要内容”的 #main），只有真实视图才切页
    if (v && v !== currentView && TITLES[v]) go(v);
  });

  document.querySelector(".sidebar").addEventListener("click", (e) => {
    const a = e.target.closest(".nav-item");
    if (a) go(a.dataset.view);
  });

  /* ---------- 总览 ---------- */
  let ov = null;
  let evalMode = "new";

  function kpi(icon, value, label, kind, sub) {
    const c = el("div", "kpi" + (kind ? " " + kind : ""));
    c.append(el("div", "ico", icon));
    const box = el("div");
    box.append(el("div", "v", value), el("div", "k", label));
    if (sub) box.append(el("div", "k-sub", sub));
    c.append(box);
    return c;
  }

  // 窄屏用列表代替缩放后的 SVG：12 个类的文字在 340px 里不可能看清
  function barList(items, keyLabel, keyValue) {
    const box = el("div", "chart-list");
    items.forEach((it) => {
      const v = Math.max(0, Math.min(1, it[keyValue] ?? 0));
      const row = el("div", "cl");
      const track = el("div", "track");
      const fill = el("i");
      fill.style.width = `${v * 100}%`;
      if (v < 0.7) fill.classList.add("low");
      track.append(fill);
      const pct = el("span", "pct" + (v < 0.7 ? " low" : ""), `${Math.round(v * 100)}%`);
      if (it.support != null) pct.title = `n=${it.support}`;
      row.append(el("span", "lab", it[keyLabel]), track, pct);
      box.append(row);
    });
    return box;
  }

  function barChart(items, keyLabel, keyValue, opts = {}) {
    const w = 900, h = 244, padL = 42, padB = 46, padT = 16;
    const ns = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(ns, "svg");
    svg.setAttribute("class", "chart");
    svg.setAttribute("viewBox", `0 0 ${w} ${h}`);
    svg.setAttribute("role", "img");
    const defs = document.createElementNS(ns, "defs");
    defs.innerHTML = '<linearGradient id="barGrad" x1="0" y1="0" x2="0" y2="1">'
      + '<stop offset="0%" stop-color="#3b82f6"/><stop offset="100%" stop-color="#93c5fd"/></linearGradient>';
    svg.append(defs);

    const plotH = h - padT - padB;
    for (let i = 0; i <= 4; i++) {
      const y = padT + plotH * (i / 4);
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

    // 参照线：总体准确率。逐类柱子和总体线一起看才知道差距在哪。
    if (opts.ref != null && isFinite(opts.ref)) {
      const ry = padT + plotH * (1 - Math.max(0, Math.min(1, opts.ref)));
      const rl = document.createElementNS(ns, "line");
      rl.setAttribute("x1", padL); rl.setAttribute("x2", w - 4);
      rl.setAttribute("y1", ry); rl.setAttribute("y2", ry);
      rl.setAttribute("stroke", "#94a3b8");
      rl.setAttribute("stroke-width", "1.2");
      rl.setAttribute("stroke-dasharray", "5 4");
      const rt = document.createElementNS(ns, "title");
      rt.textContent = `整体准确率 ${(opts.ref * 100).toFixed(1)}%`;
      rl.append(rt);
      svg.append(rl);
    }

    const n = items.length || 1;
    const bw = Math.max(8, (w - padL - 8) / n - 6);
    items.forEach((it, i) => {
      const v = Math.max(0, Math.min(1, it[keyValue] ?? 0));
      const x = padL + i * ((w - padL - 8) / n) + 3;
      const bh = plotH * v;
      const r = document.createElementNS(ns, "rect");
      r.setAttribute("x", x); r.setAttribute("y", h - padB - bh);
      r.setAttribute("width", bw); r.setAttribute("height", Math.max(1, bh));
      r.setAttribute("rx", 3);
      r.setAttribute("class", "bar" + (v < 0.7 ? " low" : ""));
      const title = document.createElementNS(ns, "title");
      title.textContent = `${it[keyLabel]}：${(v * 100).toFixed(1)}%`
        + (it.support != null ? `（n=${it.support}）` : "");
      r.append(title);
      svg.append(r);
      const t = document.createElementNS(ns, "text");
      t.setAttribute("x", x + bw / 2); t.setAttribute("y", h - padB + 15);
      t.setAttribute("class", "axis"); t.setAttribute("text-anchor", "middle");
      t.setAttribute("font-size", "11");
      t.textContent = it[keyLabel];
      svg.append(t);
      const v2 = document.createElementNS(ns, "text");
      v2.setAttribute("x", x + bw / 2); v2.setAttribute("y", h - padB + 30);
      v2.setAttribute("class", "axis"); v2.setAttribute("text-anchor", "middle");
      v2.setAttribute("font-size", "11");
      v2.setAttribute("font-weight", v < 0.7 ? "600" : "400");
      v2.setAttribute("fill", v < 0.7 ? "#d97706" : "#94a3b8");
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
    if (!ov) {
      // 首屏先用骨架占位，避免「四张空卡片 + 空图表」的闪一下
      $("kpis").replaceChildren(...[0, 1, 2, 3].map(() => {
        const box = el("div", "kpi");
        box.append(skeleton(2, 14));
        return box;
      }));
      $("accChart").replaceChildren(skeleton(6, 22));
      $("modelList").replaceChildren(skeleton(5, 16));
    }
    try {
      ov = await api("/viz/api/overview");
    } catch (err) {
      $("kpis").replaceChildren();
      $("accChart").replaceChildren(emptyState("!", "总览数据加载失败", err.message + "。稍后重试或检查 /viz/api/overview。",
        "重试", loadOverview));
      $("modelList").replaceChildren();
      return;
    }
    const perf = ov.perf.image || {};
    const oof = ov.eval.oof || {};
    const newDomain = oof.per_bucket?.new || ov.eval.new_domain || {};
    $("kpis").replaceChildren(
      kpi("▤", String(ov.dataset.total), "已标注照片", "", "12 类 · 新旧数据合计"),
      kpi("◈", ov.model.version, "当前模型", "b", "在线服务中"),
      kpi("↗", oof.top1 != null ? `${(oof.top1 * 100).toFixed(1)}%` : "—",
        "识别准确率", "b", oof.n ? `留出 ${oof.n} 张未参与训练` : ""),
      kpi("⌁", newDomain.top1 != null ? `${(newDomain.top1 * 100).toFixed(1)}%` : "—",
        "新场景准确率", "c", newDomain.n ? `新数据 ${newDomain.n} 张` : ""),
    );
    $("modelVersion").textContent = `模型 ${ov.model.version} · ${ov.model.heads} 个头`;
    renderAccChart();
    renderModels(perf);
    ensureOpsCard();
    loadOps();
  }

  /* ---------- 运行指标（/metrics） ---------- */
  function ensureOpsCard() {
    if ($("opsCard")) return;
    const card = el("div", "card");
    card.id = "opsCard";
    card.style.marginTop = "14px";
    const head = el("div", "card-head");
    head.append(el("span", "t", "服务运行指标"),
      el("span", "hint", "服务启动以来"));
    card.append(head, el("div", "ops", ""));
    card.lastChild.id = "ops";
    $("view-overview").append(card);
  }

  async function loadOps() {
    const box = $("ops");
    if (!box) return;
    box.replaceChildren(skeleton(1, 42));
    try {
      let text = await (await fetch("/metrics", { cache: "no-store" })).text();
      // 兼容旧版本：/metrics 曾把 Prometheus 文本当 JSON 字符串返回
      if (text.trim().startsWith('"')) { try { text = JSON.parse(text); } catch (_) {} }
      const pick = (key) => {
        const m = text.match(new RegExp(`^${key}\\s+([0-9.]+)`, "m"));
        return m ? Number(m[1]) : null;
      };
      const p50 = pick("spm_image_latency_ms_p50");
      const p95 = pick("spm_image_latency_ms_p95");
      const req = pick("spm_image_requests_total");
      const errs = pick("spm_image_errors_total");
      const reviews = pick("spm_image_reviews_total");
      const cells = [
        { k: "单张识别耗时（95%）", v: p95 == null ? "—" : `${Math.round(p95)} ms`,
          s: p50 == null ? "" : `一半请求在 ${Math.round(p50)} ms 内`,
          warn: p95 != null && p95 > 2000 },
        { k: "累计识别", v: req == null ? "—" : String(req), s: "含失败" },
        { k: "转人工比例", v: req && reviews != null ? `${((reviews / req) * 100).toFixed(1)}%` : "—",
          s: reviews == null ? "" : `${reviews} / ${req ?? 0} 张` },
        { k: "失败次数", v: errs == null ? "—" : String(errs), s: "服务错误 / 超时", warn: (errs || 0) > 0 },
      ];
      box.replaceChildren(...cells.map((c) => {
        const op = el("div", "op");
        const v = el("div", "v" + (c.warn ? " warn" : ""), c.v);
        op.append(el("div", "k", c.k), v);
        if (c.s) op.append(el("div", "s", c.s));
        return op;
      }));
    } catch (err) {
      box.replaceChildren(el("div", "meta", "运行指标不可用：" + err.message));
    }
  }

  function renderAccChart() {
    const oof = ov.eval.oof || {};
    const src = oof.per_class || {};
    const rows = Object.entries(src).map(([label, r]) => ({
      label: sname(label), acc: r.recall, support: r.support,
    }));
    if (!rows.length) {
      $("accChart").replaceChildren(el("div", "meta", "还没有可用的评测结果"));
      return;
    }
    const narrow = window.matchMedia("(max-width: 720px)").matches;
    const chart = narrow
      ? barList(rows, "label", "acc")
      : barChart(rows, "label", "acc", { ref: oof.top1 });
    if ($("accTitle")) {
      $("accTitle").textContent = "各类照片识别准确率"
        + (oof.top1 != null ? ` · 整体 ${(oof.top1 * 100).toFixed(1)}%` : "");
    }
    const legend = el("div", "chart-legend");
    const legendItems = [["lg-ok", "≥ 70%"], ["lg-low", "< 70% 需关注"]];
    if (!narrow) legendItems.push(["lg-ref", "整体准确率"]);
    legendItems.forEach(([cls, label]) => {
      const item = el("span", cls);
      item.append(el("i"), document.createTextNode(label));
      legend.append(item);
    });
    const note = el("div", "chart-note",
      `共 ${oof.n ?? "—"} 张未参与训练的照片；橙色表示低于 70%，建议补拍或补标。`);
    $("accChart").replaceChildren(chart, legend, note);
  }

  let narrowAcc = window.matchMedia("(max-width: 720px)").matches;
  window.addEventListener("resize", () => {
    const now = window.matchMedia("(max-width: 720px)").matches;
    if (now === narrowAcc) return;
    narrowAcc = now;
    if (ov && !$("view-overview").hidden) renderAccChart();
  });

  function renderModels(perf) {
    const list = $("modelList");
    list.replaceChildren();
    const names = { scene: "场景识别", det: "检测器（画框）" };
    const roles = ov.model.roles || [];
    // 默认只显示真正参与判定的两个模型；分组/动作等辅助头没启用时不再占一屏，
    // 也不再用“未用于判定”这种内部术语。
    const shown = ov.model.fusion_enabled
      ? roles
      : roles.filter((r) => r === "scene" || r === "det");
    shown.forEach((r) => {
      const row = el("div", "mrow");
      const box = el("div", "name");
      box.append(el("div", null, names[r] || r));
      row.append(box, el("span", "pill ok", "启用"));
      list.append(row);
    });
    if (!ov.model.fusion_enabled) {
      list.append(el("div", "meta",
        "当前只用场景识别模型给出结论；分组、动作等辅助模型未启用，不影响结果。"));
    }
    $("modelsOnline").textContent = ov.model.fusion_enabled ? "辅助模型已启用" : "运行正常";
  }

  /* ---------- 识别 ---------- */
  const CATS = {
    arrival: {
      path: "/v1/arrival", hint: "点击或拖入接车照片", name: "接车照片",
      what: "车牌、里程表、行驶证与车架 VIN 是否一致",
      fields: [
        { key: "expected_plate", label: "工单车牌（可选）", placeholder: "如 宁D09976D" },
        { key: "expected_odometer", label: "工单里程（可选，km）", type: "number", placeholder: "如 182" },
      ],
    },
    repair: {
      path: "/v1/repair", hint: "点击或拖入故障及修复照片", name: "故障及修复",
      what: "桥 / 轮胎的加油放油、有无滤芯，以及动作是否与工单一致",
      fields: [
        { key: "axle_type", label: "车桥类型", type: "select",
          options: [["", "未指定"], ["hande", "汉德桥（校验收油规则）"]] },
        { key: "expected_action", label: "工单动作", type: "select",
          options: [["", "未指定"], ["fill", "加油"], ["drain", "放油"]] },
      ],
    },
    inspection: {
      path: "/v1/inspection", hint: "点击或拖入项目检查表照片", name: "项目检查表",
      what: "服务单首页 VIN、末页签字与服务站盖章",
      fields: [
        { key: "expected_vin", label: "工单 VIN（可选）", placeholder: "17 位 VIN" },
      ],
    },
  };
  let cat = "arrival", files = [], scenes = [], urls = [], photoBoxes = [], photoFlags = [];
  let activeIdx = 0, runTimer = null;
  const inferValues = { arrival: {}, repair: {}, inspection: {} };

  /* 参数区：后端支持与工单比对，前端以前完全没有入口 */
  function renderInferParams() {
    const box = $("inferParams");
    if (!box) return;
    box.replaceChildren();
    const spec = CATS[cat];
    spec.fields.forEach((f) => {
      const wrap = el("div", "pf");
      const id = `ip-${f.key}`;
      const label = el("label", null, f.label);
      label.setAttribute("for", id);
      let input;
      if (f.type === "select") {
        input = el("select");
        (f.options || []).forEach(([value, text]) => {
          const o = el("option", null, text);
          o.value = value;
          input.append(o);
        });
        input.value = inferValues[cat][f.key] || "";
        input.addEventListener("change", () => { inferValues[cat][f.key] = input.value; });
      } else {
        input = el("input");
        input.type = f.type || "text";
        input.placeholder = f.placeholder || "";
        input.value = inferValues[cat][f.key] || "";
        input.addEventListener("input", () => { inferValues[cat][f.key] = input.value.trim(); });
      }
      input.id = id;
      wrap.append(label, input);
      box.append(wrap);
    });
    box.append(el("div", "pf-note",
      `这类照片会核对：${spec.what}。字段可以不填——只做识别；填了才会判断「与工单是否一致」。`));
  }

  function renderInferEmpty() {
    const wrap = el("div", "result-empty");
    wrap.append(emptyState("◎", "识别结论会显示在这里",
      "选好照片后点「识别」：这里给出场景判定、OCR 字段（车牌 / VIN / 里程）、"
      + "通过或需复核的结论，以及每一张的复核原因。"));
    $("out").replaceChildren(wrap);
  }

  function renderFileList() {
    const box = $("fileList");
    if (!box) return;
    box.replaceChildren();
    box.hidden = files.length === 0;
    files.forEach((f, i) => {
      const chip = el("span", "fchip");
      const name = el("b", null, f.name);
      name.title = f.name;
      const x = el("button", "x", "×");
      x.type = "button";
      x.setAttribute("aria-label", `移除 ${f.name}`);
      x.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        setFiles(files.filter((_, j) => j !== i));
      });
      chip.append(el("span", null, String(i + 1)), name, x);
      box.append(chip);
    });
  }

  $("catSeg").addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b || b.classList.contains("on")) return;
    cat = b.dataset.cat;
    [...$("catSeg").children].forEach((x) => x.classList.toggle("on", x === b));
    $("hint").textContent = CATS[cat].hint;
    files = []; scenes = []; photoBoxes = []; activeIdx = 0;
    drawPreview();
    $("run").disabled = true;
    $("run").textContent = "识别";
    $("inferImgWrap").hidden = true;
    $("drop").classList.remove("compact");
    $("inferCounter").textContent = "";
    renderFileList();
    renderInferParams();
    renderInferEmpty();
  });

  function drawPreview() {
    // 只负责把文件转成 objectURL 并释放旧的；缩略图条已按需求移除。
    urls.forEach((u) => URL.revokeObjectURL(u));
    urls = files.map((f) => URL.createObjectURL(f));
    if (activeIdx >= files.length) activeIdx = 0;
  }

  function showInferImage(i) {
    const wrap = $("inferImgWrap");
    if (!files.length) { wrap.hidden = true; $("drop").classList.remove("compact"); return; }
    activeIdx = Math.max(0, Math.min(i, files.length - 1));
    const img = $("inferImg");
    resetBroken(img);
    img.alt = files[activeIdx] ? files[activeIdx].name : "待识别照片";
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
    drawPreview();
    $("run").disabled = files.length === 0;
    $("run").textContent = "识别";
    renderFileList();
    renderInferEmpty();
    showInferImage(0);
    if (!files.length) $("hint").textContent = CATS[cat].hint;
  }
  $("files").addEventListener("change", (e) => setFiles(e.target.files));
  const drop = $("drop");
  ["dragenter", "dragover"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
  drop.addEventListener("drop", (e) => setFiles(e.dataTransfer.files));
  drop.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); $("files").click(); }
  });

  const stat = (label, value, opts = {}) => {
    const box = el("div", "stat");
    box.append(el("div", "k", label));
    const v = el("div", "v", value == null || value === "" ? "—" : String(value));
    if (opts.small) v.classList.add("sm");
    if (opts.tone) v.classList.add(opts.tone);
    box.append(v); return box;
  };

  function renderResult(d, raw) {
    const wrap = el("div", "card"); wrap.style.marginTop = "14px";
    const insideN = (d.photos || []).length;
    const outsideN = (d.unexpected || []).length;
    const reasons = d.reasons || [];

    // 只交了车头照时，整单四件套本来就不齐；这时说“可自动通过”会误导，
    // 说“需人工复核”又等于把好图判坏。用第三种状态把边界讲清楚。
    const partial = d.ok === true && d.complete === false;
    const verdict = el("div", "verdict " + (d.ok ? (partial ? "partial" : "ok") : "warn"));
    verdict.append(el("span", "v-ico", d.ok ? (partial ? "◐" : "✓") : "!"));
    const vMain = el("div");
    vMain.append(el("div", "v-t", d.ok ? (partial ? "本张通过（整单未校验）" : "可自动通过") : "需人工复核"),
      el("div", "v-s", d.ok
        ? (partial
          ? `${CATS[cat].name} ${insideN} 张已识别；整单还缺其他照片，未做一致性校验`
          : `${CATS[cat].name} ${insideN} 张，未命中拦截规则`)
        : (reasons[0] || "存在需要人工确认的项")));
    verdict.append(vMain);
    if (d.degraded) verdict.append(el("span", "tag warn", "部分照片超时"));
    wrap.append(verdict);

    const head = el("div", "result-head");
    const name = el("div", "name",
      `${CATS[cat].name} · 本类 ${insideN} 张` + (outsideN ? ` · 其他 ${outsideN} 张` : ""));
    head.append(name);
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

    if (partial && d.missing_reasons?.length) {
      wrap.append(el("div", "chart-note",
        "整单还缺：" + d.missing_reasons.join("、") +
        "。补齐后调用整单校验（/v1/workorder）才能判断能否自动通过。"));
    }

    if (reasons.length) {
      const ul = el("ul", "tips");
      reasons.forEach((r) => ul.append(el("li", null, r)));
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

    // 给上层系统对接用：结论可以直接复制走
    const tools = el("div", "result-tools");
    const copy = el("button", "ghost", "复制结果 JSON");
    copy.type = "button";
    copy.addEventListener("click", () =>
      copyText(JSON.stringify(raw ?? d, null, 2), "已复制识别结果 JSON"));
    const again = el("button", "ghost", "重新识别");
    again.type = "button";
    again.addEventListener("click", () => $("run").click());
    tools.append(copy, again);
    wrap.append(tools);

    return wrap;
  }

  function inferProgress(n, startedAt) {
    const card = el("div", "card");
    const line = el("div", "progress-line");
    const text = el("span", null, `正在识别 ${n} 张…`);
    line.append(el("span", "spin"), text);
    card.append(line,
      el("div", "chart-note", "单张通常 1–3 秒；首次请求要加载模型，可能更慢。"));
    clearInterval(runTimer);
    runTimer = setInterval(() => {
      text.textContent = `正在识别 ${n} 张… 已用 ${((Date.now() - startedAt) / 1000).toFixed(1)}s`;
    }, 200);
    return card;
  }

  function inferError(err, startedAt) {
    const card = el("div", "card result-error");
    const head = el("div", "result-head");
    head.append(el("span", "name", `识别失败 · 用时 ${((Date.now() - startedAt) / 1000).toFixed(1)}s`),
      el("span", "tag warn", "未完成"));
    card.append(head);
    card.append(el("div", "meta", err.message + (err.requestId ? `（request_id ${err.requestId}）` : "")));
    const tools = el("div", "result-tools");
    const retry = el("button", "btn", "重试");
    retry.type = "button";
    retry.addEventListener("click", () => $("run").click());
    const copy = el("button", "ghost", "复制错误信息");
    copy.type = "button";
    copy.addEventListener("click", () => copyText(`${err.message}\nrequest_id=${err.requestId || "-"}`, "已复制"));
    tools.append(retry, copy);
    card.append(tools);
    return card;
  }

  $("run").addEventListener("click", async () => {
    if (!files.length || $("run").dataset.busy) return;
    const startedAt = Date.now();
    $("run").dataset.busy = "1";
    $("run").disabled = true;
    $("run").textContent = `识别中…（${files.length} 张）`;
    $("out").replaceChildren(inferProgress(files.length, startedAt));
    const fd = new FormData();
    files.forEach((f) => fd.append("images", f));
    Object.entries(inferValues[cat] || {}).forEach(([k, v]) => {
      if (v != null && String(v).trim() !== "") fd.append(k, v);
    });
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
      clearInterval(runTimer);
      drawPreview();
      showInferImage(0);
      $("out").replaceChildren(renderResult(body, body));
      $("out").scrollIntoView({ behavior: "smooth", block: "nearest" });
      if (body.degraded) toast("部分照片超时，结果不完整", "bad");
    } catch (err) {
      clearInterval(runTimer);
      $("out").replaceChildren(inferError(err, startedAt));
      if (err.code === "BUSY" || err.code === "RATE_LIMITED") {
        toast(err.message || "服务繁忙，稍后再试", "bad");
      }
    } finally {
      clearInterval(runTimer);
      delete $("run").dataset.busy;
      $("run").disabled = false;
      $("run").textContent = "识别";
    }
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
      resetBroken(img);
      img.alt = it.name || "样本大图";
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

  /* ---------- 数据集：上传 + 自动标注 + 逐张人工确认 ---------- */
  let dsCurrent = null;
  let dsDetail = null;
  const dsChanges = new Map();

  function dsUpdateBar() {
    const bar = $("dsDetailBar");
    if (!bar) return;
    bar.hidden = dsChanges.size === 0;
    $("dsDetailChanged").textContent = `已修改 ${dsChanges.size} 张`;
  }

  function renderOrphanBanner(d) {
    const box = $("dsBanner");
    box.replaceChildren();
    const n = d.orphan_images || 0;
    if (!n) return;
    const names = (d.orphans || []).map((o) => `${o.batch}（${o.count} 张）`).join("、");
    const banner = el("div", "banner-warn");
    banner.append(el("span", null,
      `有 ${n} 条历史自动标注记录对应的图片已不存在：${names}。` +
      "这不是挂载问题，是批次目录被删过；清理后不会再出现在待复核队列。"));
    const btn = el("button", "ghost", "清理失效记录");
    btn.style.marginLeft = "10px";
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      try {
        const r = await api("/viz/api/dataset/cleanup", { method: "POST" });
        toast(`已清理 ${r.removed} 条失效记录`);
        loadDataset();
      } catch (err) { toast("清理失败：" + err.message, "bad"); btn.disabled = false; }
    });
    banner.append(btn);
    box.append(banner);
  }

  async function loadDataset() {
    const box = $("dsBatches");
    const cnt = $("dsCount");
    try {
      const d = await api("/viz/api/dataset/batches");
      const list = d.batches || [];
      renderOrphanBanner(d);
      cnt.textContent = `共 ${list.length} 批`;
      box.replaceChildren();
      if (!list.length) {
        box.append(emptyState("▤", "还没有上传过数据集",
          "把按类别建好目录的 zip 拖到上面的上传区，目录名会自动映射到 12 个场景类。"));
        return;
      }
      list.forEach((b) => {
        const card = el("div", "ds-batch");
        const top = el("div", "top");
        const name = el("span", "name", b.batch);
        name.style.cursor = "pointer";
        name.addEventListener("click", () => openDatasetBatch(b.batch));
        top.append(name,
                   el("span", "when", (b.created_at || "").replace("T", " ").slice(0, 16)),
                   el("span", "when", `${b.images} 张`));
        const acts = el("div", "acts");
        const stat = el("span", "batch-stat", "");

        const view = el("button", "ghost", "查看 / 改标");
        view.addEventListener("click", () => openDatasetBatch(b.batch));

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

        const del = el("button", "ghost", "删除");
        del.addEventListener("click", async () => {
          if (!confirm(`删除批次 ${b.batch}？\n\n会同时删除这批图片、自动标注和人工结论，不可恢复。`)) return;
          del.disabled = true;
          try {
            await api(`/viz/api/dataset/${encodeURIComponent(b.batch)}`, { method: "DELETE" });
            if (dsCurrent === b.batch) closeDatasetDetail();
            toast(`已删除 ${b.batch}`);
            loadDataset();
          } catch (err) { toast("删除失败：" + err.message, "bad"); del.disabled = false; }
        });
        acts.append(stat, view, btn, del);
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

  function closeDatasetDetail() {
    dsCurrent = null;
    dsDetail = null;
    dsChanges.clear();
    $("dsDetailCard").hidden = true;
    $("dsDetailBody").replaceChildren();
    dsUpdateBar();
  }

  function renderDatasetDetail(d) {
    const body = $("dsDetailBody");
    body.replaceChildren();
    const grid = el("div", "lgrid");
    (d.items || []).forEach((it) => {
      const card = el("div", "lcard");
      const shot = el("div", "lshot");
      const img = el("img");
      img.loading = "lazy";
      img.src = it.url;
      img.alt = it.name;
      shot.append(img);
      const info = el("div", "lbody");
      info.append(el("div", "lname", it.name));

      const row0 = el("div", "lrow");
      row0.append(el("span", "lpred", `目录：${sname(it.dir_label)}`));
      if (it.auto_label) {
        const conf = it.confidence != null ? ` ${(it.confidence * 100).toFixed(0)}%` : "";
        row0.append(el("span", "lpred" + (it.auto_label !== it.dir_label ? " bad" : ""),
          `预标：${sname(it.auto_label)}${conf}`));
      } else {
        row0.append(el("span", "lpred", "未自动标注"));
      }
      info.append(row0);

      const row = el("div", "lrow");
      const sel = el("select");
      const opts = [...(d.classes || []), { slug: "uncertain", name: "无法判断（不用于训练）" },
        { slug: "exclude", name: "不算这一类（不用于训练）" }];
      opts.forEach((c) => sel.append(new Option(c.name, c.slug)));
      sel.value = it.final_label || it.dir_label;
      sel.addEventListener("change", () => {
        const base = it.final_label || it.dir_label;
        if (sel.value === base) dsChanges.delete(it.path);
        else dsChanges.set(it.path, { path: it.path, label: sel.value });
        dsUpdateBar();
      });
      row.append(sel);
      if (it.reviewed) row.append(el("span", "lpred", it.corrected ? "已改标" : "已确认"));
      info.append(row);
      card.append(shot, info);
      grid.append(card);
    });
    body.append(grid);
    $("dsDetailStats").textContent =
      `${d.stats.images} 张 · 已自动标注 ${d.stats.auto_labeled} · 已人工确认 ${d.stats.reviewed} · 改标 ${d.stats.corrected}`;
  }

  async function openDatasetBatch(batch) {
    try {
      const d = await api(`/viz/api/dataset/${encodeURIComponent(batch)}?limit=500`);
      dsCurrent = batch;
      dsDetail = d;
      dsChanges.clear();
      $("dsDetailCard").hidden = false;
      $("dsDetailTitle").textContent = `批次 ${batch}`;
      renderDatasetDetail(d);
      dsUpdateBar();
      $("dsDetailCard").scrollIntoView({ block: "start", behavior: "smooth" });
    } catch (err) { toast("打开批次失败：" + err.message, "bad"); }
  }

  $("dsDetailClose").addEventListener("click", closeDatasetDetail);
  $("dsDetailReset").addEventListener("click", () => {
    dsChanges.clear();
    dsUpdateBar();
    if (dsCurrent) openDatasetBatch(dsCurrent);
  });
  $("dsDetailSave").addEventListener("click", async () => {
    if (!dsCurrent || !dsChanges.size) return;
    const btn = $("dsDetailSave");
    btn.disabled = true;
    try {
      const r = await api(`/viz/api/dataset/${encodeURIComponent(dsCurrent)}/labels`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ items: [...dsChanges.values()] }),
      });
      toast(`已保存 ${r.saved} 张`);
      dsChanges.clear();
      await loadDataset();
      if (dsCurrent) await openDatasetBatch(dsCurrent);
    } catch (err) { toast("保存失败：" + err.message, "bad"); }
    finally { btn.disabled = false; }
  });

  async function uploadDataset(file) {
    const out = $("dsResult");
    const started = Date.now();
    const line = el("div", "progress-line");
    const text = el("span", null, `上传中… ${(file.size / 1024 / 1024).toFixed(1)}MB`);
    line.append(el("span", "spin"), text);
    out.replaceChildren(line);
    const tick = setInterval(() => {
      text.textContent = `上传中… ${(file.size / 1024 / 1024).toFixed(1)}MB · 已用 `
        + `${((Date.now() - started) / 1000).toFixed(0)}s`;
    }, 500);
    const fd = new FormData();
    fd.append("file", file);
    try {
      const r = await fetch("/viz/api/dataset/upload", { method: "POST", body: fd });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || `HTTP ${r.status}`);
      const dist = Object.entries(d.classes || {}).map(([k, v]) => `${sname(k)} ${v}`).join("、");
      out.replaceChildren(el("div", "meta",
        `已导入 ${d.images} 张（${(d.bytes / 1024 / 1024).toFixed(1)}MB）：${dist}`));
      toast(`已导入 ${d.images} 张图片`);
      loadDataset();
    } catch (err) {
      out.replaceChildren(el("div", "banner", "上传失败：" + err.message));
    } finally { clearInterval(tick); }
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
    const twrap = el("div", "tblwrap");
    twrap.append(t);
    box.append(twrap);
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
    $("mGrid").replaceChildren(skeleton(5, 26));
    $("mFoot").replaceChildren();
    try { mData = await api(`/viz/api/mistakes?source=${mSource}`); }
    catch (err) {
      $("mGrid").replaceChildren(emptyState("!", "误判数据加载失败", err.message, "重试", loadMistakes));
      return;
    }
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
      $("mGrid").replaceChildren(emptyState("✓", "这个范围没有误判",
        "换一个数据源看看，或去「快速标注」继续处理待复核样本。"));
    }
  }

  const M_PAGE = 24;
  let mShown = 0;

  function renderMFoot() {
    const foot = $("mFoot");
    if (!foot) return;
    foot.replaceChildren();
    if (!mGroup) return;
    const total = mGroup.items.length;
    foot.append(el("span", null,
      `已选 ${selected.size} / ${total} 张 · 已显示 ${Math.min(mShown, total)} 张`));
    if (mShown < total) {
      const more = el("button", "ghost", `加载更多（还有 ${total - mShown} 张）`);
      more.type = "button";
      more.addEventListener("click", () => { appendMistakeCards(); renderMFoot(); });
      foot.append(more);
    }
  }

  function appendMistakeCards() {
    if (!mGroup) return;
    const grid = $("mGrid");
    const g = mGroup;
    const slice = g.items.slice(mShown, mShown + M_PAGE);
    slice.forEach((it) => {
      const idx = g.items.indexOf(it);
      const card = el("div", "lcard");
      if (selected.has(it.path)) card.classList.add("sel");

      const shot = el("div", "lshot");
      const img = el("img");
      img.loading = "lazy";
      img.alt = `${it.file}（应为 ${sname(it.want)}，判成 ${sname(it.got)}）`;
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
      chk.checked = selected.has(it.path);
      chk.setAttribute("aria-label", `选择 ${it.file}`);
      chk.addEventListener("change", () => {
        if (chk.checked) selected.add(it.path); else selected.delete(it.path);
        card.classList.toggle("sel", chk.checked);
        $("mSave").disabled = selected.size === 0;
        $("mRelabel").disabled = selected.size === 0;
        renderMFoot();
      });
      row.append(chk,
        el("span", "lpred bad", `判成 ${sname(it.got)}`),
        el("span", "lpred", `${((it.conf ?? 0) * 100).toFixed(0)}%`));
      body.append(row);
      body.append(el("div", "lname", `应为 ${sname(it.want)}`));

      card.append(shot, body);
      grid.append(card);
    });
    mShown = Math.min(mShown + slice.length, g.items.length);
  }

  function openGroup(g) {
    mGroup = g; selected = new Set(); mShown = 0;
    $("mSave").disabled = true; $("mRelabel").disabled = true;
    $("mTarget").value = g.want;
    $("mTitle").textContent = `${sname(g.want)} → ${sname(g.got)} · ${g.n} 张 · 平均置信度 ${g.avg_conf}`;

    const grid = $("mGrid");
    grid.replaceChildren();
    grid.className = "lgrid";          // 复用标注页的卡片网格，避免窄列竖排文字
    appendMistakeCards();
    renderMFoot();
  }

  $("mSelectAll").addEventListener("click", () => {
    if (!mGroup) return;
    const all = selected.size === mGroup.items.length;
    // 全选针对整组，而不只是已渲染的前 24 张——否则「全选本组」名不副实
    selected = all ? new Set() : new Set(mGroup.items.map((i) => i.path));
    [...$("mGrid").children].forEach((c, i) => {
      const chk = c.querySelector("input[type=checkbox]");
      const path = mGroup.items[i]?.path;
      if (chk) chk.checked = !all;
      c.classList.toggle("sel", !all && !!path && selected.has(path));
    });
    $("mSave").disabled = selected.size === 0;
    $("mRelabel").disabled = selected.size === 0;
    renderMFoot();
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
      toast(`已加入改标计划：${d.added} 张（只登记，不搬文件）`);
    } catch (err) { toast("加入失败：" + err.message, "bad"); }
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
      toast(`已登记 ${d.added} 张难例`);
    } catch (err) { toast("登记失败：" + err.message, "bad"); }
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

  // 上传批次被重复导入时，同一张图会在队列里出现多次。
  // 文件名含工单号和时间戳，足够作为去重键；保留置信度最低的那条优先复核。
  function dedupeQueue(items) {
    const byFile = new Map();
    items.forEach((it) => {
      const key = it.file || it.path;
      const prev = byFile.get(key);
      if (!prev || (it.conf ?? 1) < (prev.conf ?? 1)) byFile.set(key, it);
    });
    return [...byFile.values()];
  }

  function labelBanner(d, dup) {
    const box = $("lBanner");
    box.replaceChildren();
    const add = (text, cls) => {
      const b = el("div", "banner" + (cls ? " " + cls : ""));
      b.append(el("span", "b-t", text));
      box.append(b);
    };
    if (d?.missing) {
      add(`${d.missing} 张历史图片的文件已不存在，已从队列跳过。` +
        "如果是换机部署，先确认 data/ 目录挂载完整。");
    }
    if (dup) add(`已自动合并 ${dup} 张重复上传的同一图片（多批上传了相同文件）。`, "info");
  }

  async function loadLabelQueue() {
    lbFam = $("lFam").querySelector(".on")?.dataset.fam || "oil";
    $("lProgress").textContent = "加载中…";
    $("lBanner").replaceChildren();
    $("lGrid").replaceChildren(...[0, 1, 2].map(() => {
      const card = el("div", "lcard");
      const shot = el("div", "lshot skeleton");
      shot.style.height = "180px";
      card.append(shot);
      return card;
    }));
    let d;
    try {
      d = await api(`/viz/api/review_queue?source=all&family=${lbFam}&limit=600`);
    } catch (err) {
      $("lGrid").replaceChildren();
      $("lGrid").append(emptyState("!", "待复核队列加载失败", err.message, "重试", loadLabelQueue));
      $("lProgress").textContent = "—";
      return;
    }
    const rawItems = d.items || [];
    lbItems = dedupeQueue(rawItems);
    const dup = rawItems.length - lbItems.length;
    lbShown = 0; lbSelected = new Set();
    updateBatchBar();
    $("lGrid").replaceChildren();
    labelBanner(d, dup);
    $("lProgress").textContent = `共 ${lbItems.length} 张待复核`;
    if (!lbItems.length) {
      $("lGrid").append(emptyState("✓", "这个范围没有待复核的图片",
        "换一个分组，或去「错误样本」看看模型判错的样本。"));
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
    img.alt = `${it.file}（预判 ${sname(it.pred)}，原标签 ${sname(it.labeled)}）`;
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
      toast("保存失败：" + err.message, "bad");
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
      toast("批量保存失败：" + err.message, "bad");
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

  async function loadTrainingDatasets() {
    const sel = $("trData");
    const keep = sel.value;
    sel.replaceChildren();
    sel.append(new Option("全部历史数据（自动 70/15/15）", "all"));
    try {
      const d = await api("/viz/api/dataset/training");
      (d.datasets || []).forEach((ds) => {
        const c = ds.counts || {};
        sel.append(new Option(
          `${ds.name} · 训练 ${c.train ?? 0} / 验证 ${c.val ?? 0} / 测试 ${c.test ?? 0}`,
          `built:${ds.name}`));
      });
    } catch {}
    try {
      const d = await api("/viz/api/dataset/batches");
      (d.batches || []).forEach((b) => {
        sel.append(new Option(`上传批次 ${b.batch}（${b.images} 张，自动 70/15/15）`,
          `upload:${b.batch}`));
      });
    } catch {}
    if ([...sel.options].some((o) => o.value === keep)) sel.value = keep;
    showTrainDataInfo();
  }

  function showTrainDataInfo() {
    const value = $("trData").value;
    if (value === "all") {
      $("trDataInfo").textContent = "开始训练时会自动合并新旧数据并按工单分组切分。";
      return;
    }
    $("trDataInfo").textContent = value.startsWith("upload:")
      ? "开始训练时会把这一批按 70/15/15 切分，人工改过的标签优先。"
      : "";
  }

  async function initTrainPanel() {
    await loadTrainingDatasets();
  }

  function renderTrain(j) {
    const box = $("trStatus");
    if (!j) { box.replaceChildren(el("div", "meta", "当前没有训练任务")); return; }
    const label = { preparing: "准备中", running: "训练中", completed: "已完成",
                    failed: "失败", cancelled: "已取消" }[j.status] || j.status;
    const pct = j.epochs ? Math.min(100, Math.round((j.epoch / j.epochs) * 100)) : 0;
    const tone = j.status === "completed" ? "ok" : j.status === "failed" ? "warn" : "";
    const head = el("div", null,
      `状态：${label}　第 ${j.epoch}/${j.epochs} 轮` +
      (j.top1 != null ? `　验证集准确率 ${(j.top1 * 100).toFixed(1)}%` : ""));
    head.className = "meta " + tone;
    const bar = el("div", "trainbar");
    const fill = el("i"); fill.style.width = pct + "%"; bar.append(fill);
    box.replaceChildren(head, bar);
    const meta = [];
    if (j.dataset_name) {
      const c = j.dataset_counts || {};
      meta.push(`数据：${j.dataset_name}` +
        (c.train != null ? `（训练 ${c.train} / 验证 ${c.val} / 测试 ${c.test}）` : ""));
    }
    if (j.device) meta.push(`设备：${j.device}`);
    if (meta.length) box.append(el("div", "meta", meta.join(" · ")));
    if (j.test?.available) {
      box.append(el("div", "meta ok",
        `测试集：${(j.test.top1 * 100).toFixed(1)}%（${j.test.n} 张，未参与训练/调参）`));
    } else if (j.status === "completed") {
      box.append(el("div", "banner-warn",
        "这次训练没有测试集结果，不能作为上线依据。请用“全部历史数据”重新训练。"));
    }
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
        const acts = el("div");
        acts.style.marginTop = "8px";
        const btn = el("button", "btn", "注册为候选版本");
        btn.addEventListener("click", async () => {
          if (!d.job.test?.available) {
            if (!confirm("这次训练没有测试集结果。仍要注册为候选版本吗？（不建议上线）")) return;
          }
          const ver = prompt("版本号（vX.Y.Z）", "v5.2.0");
          if (!ver) return;
          const metrics = { scene: {
            top1: d.job.test?.top1 ?? d.job.top1,
            val_n: d.job.test?.n,
            note: d.job.test?.available
              ? `独立测试集 ${d.job.test.n} 张（未参与训练/调参）`
              : "无独立测试集，仅验证集",
          } };
          try {
            await api("/viz/api/model/register", {
              method: "POST", headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ version: ver, weights: d.job.weights, metrics }),
            });
            $("modelHint").textContent = `已注册 ${ver}（候选，可在下方切换）`;
            loadModelVersions();
          } catch (err) { toast("注册失败：" + err.message, "bad"); }
        });
        const clear = el("button", "ghost", "清除这次记录");
        clear.style.marginLeft = "8px";
        clear.addEventListener("click", async () => {
          if (!confirm("清除训练记录和本次 run 目录？权重文件会被删除。")) return;
          try {
            await api("/viz/api/train/delete", {
              method: "POST", headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ job_id: d.job.job_id, delete_run: true }),
            });
            pollTrain();
          } catch (err) { toast("清除失败：" + err.message, "bad"); }
        });
        acts.append(btn, clear);
        $("trStatus").append(acts);
      }
    } catch (err) {
      renderTrain(null);
    }
  }

  $("trData").addEventListener("change", showTrainDataInfo);

  $("trCleanTests").addEventListener("click", async () => {
    const btn = $("trCleanTests");
    btn.disabled = true;
    try {
      const r = await api("/viz/api/train/cleanup_tests", { method: "POST" });
      toast(r.removed?.length ? `已清理 ${r.removed.length} 条测试记录` : "没有需要清理的测试记录");
      pollTrain();
    } catch (err) { toast("清理失败：" + err.message, "bad"); }
    finally { btn.disabled = false; }
  });

  $("trBuild").addEventListener("click", async () => {
    const btn = $("trBuild");
    btn.disabled = true; btn.textContent = "准备中…";
    try {
      const r = await api("/viz/api/dataset/build", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ source: "all", include_uploads: true }),
      });
      $("trDataInfo").textContent =
        `已准备 ${r.name}：训练 ${r.counts.train} / 验证 ${r.counts.val} / 测试 ${r.counts.test}` +
        (r.warnings?.length ? `（${r.warnings.join("；")}）` : "");
      await loadTrainingDatasets();
      $("trData").value = `built:${r.name}`;
    } catch (err) {
      toast("准备失败：" + err.message, "bad");
    } finally { btn.disabled = false; btn.textContent = "准备训练集"; }
  });

  $("trStart").addEventListener("click", async () => {
    const dataset = $("trData").value || "all";
    const body = {
      dataset,
      epochs: Number($("trEpochs").value || 25),
      imgsz: Number($("trImgsz").value || 512),
      include_uploads: true,
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
      toast("启动失败：" + err.message, "bad");
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
    if (d.models_writable === false) {
      box.append(el("div", "banner-warn",
        "模型目录是只读挂载：可以查看，但不能切换或删除。请把 api 容器的 models 挂载改成可写后重建。"));
    }
    const t = el("table", "tbl");
    const hr = el("tr");
    ["版本", "状态", "准确率", "评测数据", "创建时间", ""].forEach((x) =>
      hr.append(el("th", null, x)));
    const thead = el("thead"); thead.append(hr); t.append(thead);
    const tb = el("tbody");
    (d.versions || []).forEach((v) => {
      const tr = el("tr");
      const name = el("td", null, v.version);
      const st = el("td");
      const stLabel = v.active ? "使用中" : (v.status === "retired" ? "历史版本"
        : v.status === "candidate" ? "候选" : (v.status || "—"));
      st.append(el("span", "pill " + (v.active ? "ok" : ""), stLabel));
      const top1 = v.metrics?.scene?.top1;
      const m = el("td", "num", top1 != null ? `${(top1 * 100).toFixed(2)}%` : "—");
      const sc = v.metrics?.scene || {};
      const scopeTd = el("td", "num");
      if (sc.val_n) {
        const isTest = /测试集/.test(sc.note || "");
        const isOld = /单折|折外/.test(sc.note || "");
        scopeTd.append(document.createTextNode(
          `${isTest ? "独立测试集" : isOld ? "旧评测集" : "验证集"} ${sc.val_n} 张`));
        if (isOld) scopeTd.title = "这个版本用的是旧评测集，和当前版本不能直接比大小";
      } else {
        scopeTd.textContent = "—";
      }
      const when = el("td", "num", (v.created_at || "").replace("T", " ").slice(0, 16));
      const act = el("td");
      if (!v.active && v.exists && d.models_writable !== false) {
        const btn = el("button", "ghost", "设为线上");
        btn.addEventListener("click", async () => {
          if (!confirm(`把线上模型切到 ${v.version}？\n\n切换会立即热加载，无需重启。`)) return;
          btn.disabled = true;
          try {
            const r2 = await api("/viz/api/model/activate", {
              method: "POST", headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ version: v.version }),
            });
            $("modelHint").textContent = r2?.reloaded
              ? `已切到 ${v.version}，已生效`
              : `已切到 ${v.version}，需重启 vision 服务`;
            loadModelVersions();
          } catch (err) {
            toast("切换失败：" + err.message, "bad");
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
          } catch (err) {
            const retry = confirm(`删除失败：${err.message}\n\n只从列表移除、保留权重文件？`);
            if (!retry) return;
            try {
              await api("/viz/api/model/delete", {
                method: "POST", headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ version: v.version, delete_files: false }),
              });
              $("modelHint").textContent = `已从列表移除 ${v.version}（权重文件保留）`;
              loadModelVersions();
            } catch (err2) { toast("移除失败：" + err2.message, "bad"); }
          }
        });
        act.append(del);
      }
      tr.append(name, st, m, scopeTd, when, act);
      tb.append(tr);
    });
    t.append(tb);
    const wrap = el("div", "tblwrap");
    wrap.append(t);
    box.replaceChildren(wrap);
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
      // 上限直接写出来，避免用户一次拖 30 张才发现被拒
      const lim = m.limits || {};
      const box = $("inferLimits");
      if (box && lim.max_photos) {
        box.textContent = `一次最多 ${lim.max_photos} 张 · 单张 ≤ ${lim.max_image_mb}MB · `
          + `合计 ≤ ${lim.max_total_mb}MB · 单次超时 ${lim.service_timeout_s}s`;
      }
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
    renderInferParams();
    renderInferEmpty();
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
