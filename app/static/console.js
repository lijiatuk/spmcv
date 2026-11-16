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
  function go(view) {
    if (!TITLES[view]) view = "overview";
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
    if (view === "model") loadModelVersions();
    if (view === "train") loadTrain();
  }
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
  let cat = "arrival", files = [], scenes = [], urls = [], photoBoxes = [];

  $("catSeg").addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b || b.classList.contains("on")) return;
    cat = b.dataset.cat;
    [...$("catSeg").children].forEach((x) => x.classList.toggle("on", x === b));
    $("hint").textContent = CATS[cat].hint;
    files = []; scenes = []; photoBoxes = []; drawPreview(); $("run").disabled = true; $("out").replaceChildren();
  });

  function drawPreview() {
    urls.forEach((u) => URL.revokeObjectURL(u));
    urls = [];
    $("preview").replaceChildren();
    files.forEach((f, i) => {
      const url = URL.createObjectURL(f); urls.push(url);
      const box = el("div", "shot");
      box.style.cursor = "pointer";
      const img = el("img"); img.src = url; img.alt = f.name;
      const got = scenes[i];
      const cap = el("span", "cap" + (got ? (got === "outside" ? " bad" : " ok") : ""),
        got ? (got === "outside" ? "不在本类" : sname(got)) : "");
      box.append(img, cap);
      // 点缩略图 → 大图看标注，不再跳新标签页
      box.addEventListener("click", () => {
        viewer.open(
          files.map((ff, k) => ({
            src: urls[k], title: sname(scenes[k]) || "未识别", name: ff.name,
            meta: [["场景", sname(scenes[k])], ["位置", `${k + 1} / ${files.length}`]],
            idx: k,
          })),
          i,
          (it) => photoBoxes[it.idx] || []
        );
      });
      $("preview").append(box);
    });
  }

  function setFiles(list) {
    files = Array.from(list).filter((f) => f.type.startsWith("image/"));
    scenes = []; photoBoxes = []; $("files").value = "";
    drawPreview(); $("run").disabled = files.length === 0; $("out").replaceChildren();
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
    const head = el("div", "result-head");
    head.append(el("div", "name", `${CATS[cat].name} · ${(d.photos || []).length} 张`),
      el("span", "tag " + (d.ok ? "ok" : "warn"), d.ok ? "通过" : "复核"));
    wrap.append(head);

    const seen = [...new Set((d.photos || []).map((p) => sname(p.scene)))].filter(Boolean);
    if (seen.length) wrap.append(el("div", "seen", "识别为：" + seen.join("、")));

    const s = el("div", "stats");
    if (cat === "arrival") {
      s.append(stat("车牌", d.plate, { small: true }), stat("里程", d.odometer != null ? d.odometer + " km" : null),
        stat("VIN", d.vin.match ? "一致" : (d.vin.license || d.vin.frame ? "不一致" : null),
          { small: true, tone: d.vin.match ? "ok" : "warn" }));
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
      const outside = new Set((body.unexpected || []).map((p) => p.index));
      [...(body.photos || []), ...(body.unexpected || [])].forEach((p) => {
        if (typeof p.index !== "number") return;
        scenes[p.index] = outside.has(p.index) ? "outside" : p.scene;
        photoBoxes[p.index] = p.boxes || [];
      });
      drawPreview();
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
        paintBoxes($("vBoxes"), boxes || [], img);
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
  function paintBoxes(container, boxes, imgEl) {
    container.replaceChildren();
    if (!boxes || !boxes.length) return;
    const nw = imgEl.naturalWidth, nh = imgEl.naturalHeight;
    const rect = imgEl.getBoundingClientRect();
    if (!nw || !nh || !rect.width) return;
    // 图片在容器里可能居中留白（object-fit: contain），必须减去偏移
    const host = container.getBoundingClientRect();
    const ox = rect.left - host.left;
    const oy = rect.top - host.top;
    const sx = rect.width / nw;
    const sy = rect.height / nh;
    boxes.forEach((b) => {
      const [x1, y1, x2, y2] = b.box;
      const d = el("div", "bx");
      d.style.left = `${ox + x1 * sx}px`;
      d.style.top = `${oy + y1 * sy}px`;
      d.style.width = `${(x2 - x1) * sx}px`;
      d.style.height = `${(y2 - y1) * sy}px`;
      d.append(el("span", null, `${b.label} ${b.score.toFixed(2)}`));
      container.append(d);
    });
  }

  /* ---------- 数据集 ---------- */
  const PAGE = 60;              // 一次只渲染 60 行，多的点"加载更多"
  let dsCache = new Map();
  let dsAll = [];               // 当前类的全部图片
  let dsFiles = [];             // 过滤后的列表
  let dsShown = 0;

  function resetCards() {
    const q = ($("q").value || "").trim().toLowerCase();
    dsFiles = q ? dsAll.filter((f) => f.split("/").pop().toLowerCase().includes(q)) : dsAll.slice();
    dsShown = 0;
    $("cards").replaceChildren();
    const old = $("cards").parentElement.querySelector(".more");
    if (old) old.remove();
    if (!dsFiles.length) {
      $("cards").append(el("div", "meta", dsAll.length ? "没有匹配的文件" : "这个目录没有图片"));
      return;
    }
    appendCards();
  }

  async function detectOf(path) {
    if (!dsCache.has(path)) {
      try { dsCache.set(path, (await api(`/viz/api/detect?path=${encodeURIComponent(path)}`)).boxes); }
      catch { dsCache.set(path, []); }
    }
    return dsCache.get(path);
  }

  async function loadDataset() {
    if (loaded.has("dataset")) return;
    loaded.add("dataset");
    const d = await api("/viz/api/classes");
    const sel = $("root"); sel.replaceChildren();
    (d.roots || []).forEach((r) => {
      const o = el("option", null, r.split("/").slice(-1)[0]);
      o.value = r; if (r === d.root) o.selected = true; sel.append(o);
    });
    renderClasses(d.classes);
  }

  function renderClasses(classes) {
    const box = $("classes"); box.replaceChildren();
    classes.forEach((c) => {
      const row = el("div", "cls");
      row.append(el("span", null, c.name), el("span", null, String(c.count)));
      row.addEventListener("click", async () => {
        [...box.children].forEach((x) => x.classList.toggle("on", x === row));
        $("clsTitle").textContent = `${c.name}`;
        $("clsHint").textContent = `${c.count} 张 · 点任意一行查看大图与标注`;
        const d = await api(`/viz/api/images?dir=${encodeURIComponent(c.path)}`);
        dsAll = d.files;
        $("q").value = "";
        resetCards();
      });
      box.append(row);
    });
  }

  function appendCards() {
    const box = $("cards");
    const slice = dsFiles.slice(dsShown, dsShown + PAGE);
    slice.forEach((p, k) => {
      const idx = dsShown + k;
      const row = el("div", "card-row");
      const img = el("img", "thumb-s");
      img.loading = "lazy";
      img.src = `/viz/api/file?path=${encodeURIComponent(p)}`;
      const info = el("div", "info");
      info.append(el("div", "line1", p.split("/").pop()), el("div", "line2", `第 ${idx + 1} 张`));
      row.append(img, info, el("span", "go", "›"));
      row.addEventListener("click", () => {
        viewer.open(
          dsFiles.map((path) => ({
            src: `/viz/api/file?path=${encodeURIComponent(path)}`,
            title: "数据集样本",
            name: path.split("/").pop(),
            meta: [["路径", path.replace(/^.*?\/data\//, "data/")]],
            path,
          })),
          idx,
          (it) => detectOf(it.path)
        );
      });
      box.append(row);
    });
    dsShown += slice.length;

    const old = box.parentElement.querySelector(".more");
    if (old) old.remove();
    if (dsShown < dsFiles.length) {
      const more = el("div", "more");
      const btn = el("button", null, `加载更多（还有 ${dsFiles.length - dsShown} 张）`);
      btn.addEventListener("click", () => appendCards());
      more.append(btn);
      box.after(more);
    }
  }

  $("q").addEventListener("input", () => { if (dsAll.length) resetCards(); });
  $("root").addEventListener("change", async (e) => {
    $("classes").replaceChildren(); $("cards").replaceChildren();
    dsFiles = []; dsShown = 0;
    const d = await api(`/viz/api/classes?root=${encodeURIComponent(e.target.value)}`);
    renderClasses(d.classes);
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
    $("mTitle").textContent = `共 ${mData.total} 张误判，选一个分组`;
    $("mSave").disabled = true;
  }

  function openGroup(g) {
    mGroup = g; selected = new Set();
    $("mSave").disabled = true; $("mRelabel").disabled = true;
    $("mTarget").value = g.want;
    $("mTitle").textContent = `${sname(g.want)} → ${sname(g.got)} · ${g.n} 张`;
    const grid = $("mGrid"); grid.replaceChildren();

    g.items.forEach((it, idx) => {
      const row = el("div", "card-row");
      const thumb = el("img", "thumb-s");
      thumb.loading = "lazy";
      thumb.src = `/viz/api/file?path=${encodeURIComponent(it.path)}`;

      const info = el("div", "info");
      info.append(el("div", "line1", it.file));
      const l2 = el("div", "line2");
      l2.append(el("span", "tag2 bad", `判成 ${sname(it.got)}`),
                el("span", "tag2", `${(it.conf * 100).toFixed(0)}%`),
                el("span", "tag2", `应为 ${sname(it.want)}`));
      info.append(l2);

      const chk = el("input"); chk.type = "checkbox";
      chk.addEventListener("click", (e) => {
        e.stopPropagation();
        if (chk.checked) selected.add(it.path); else selected.delete(it.path);
        row.style.borderColor = chk.checked ? "var(--accent)" : "";
        $("mSave").disabled = selected.size === 0;
        $("mRelabel").disabled = selected.size === 0;
      });

      row.append(thumb, info, chk);
      row.addEventListener("click", () => {
        viewer.open(
          g.items.map((x) => ({
            src: `/viz/api/file?path=${encodeURIComponent(x.path)}`,
            title: `应为 ${sname(x.want)}`,
            name: x.file,
            meta: [["判定", sname(x.got)], ["置信度", `${(x.conf * 100).toFixed(1)}%`],
                   ["目录", x.folder]],
            path: x.path,
          })),
          idx,
          (x) => detectOf(x.path)
        );
      });
      grid.append(row);
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
          if (im.complete && im.naturalWidth) paintBoxes(o, boxes || [], im);
          else im.addEventListener("load", () => paintBoxes(o, boxes || [], im), { once: true });
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
    ["版本", "状态", "场景 top1", "创建时间", ""].forEach((x) => hr.append(el("th", null, x)));
    const thead = el("thead"); thead.append(hr); t.append(thead);
    const tb = el("tbody");
    (d.versions || []).forEach((v) => {
      const tr = el("tr");
      const name = el("td", null, v.version);
      const st = el("td");
      st.append(el("span", "pill " + (v.active ? "ok" : ""), v.active ? "使用中" : (v.status || "—")));
      const top1 = v.metrics?.scene?.top1;
      const m = el("td", "num", top1 != null ? `${(top1 * 100).toFixed(2)}%` : "—");
      const when = el("td", "num", (v.created_at || "").replace("T", " ").slice(0, 16));
      const act = el("td");
      if (!v.active && v.exists) {
        const btn = el("button", "ghost", "切换到此版本");
        btn.addEventListener("click", async () => {
          if (!confirm(`把线上模型切到 ${v.version}？\n\n切换只改指针，需重启 vision 服务才生效。`)) return;
          btn.disabled = true;
          try {
            await api("/viz/api/model/activate", {
              method: "POST", headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ version: v.version }),
            });
            $("modelHint").textContent = `已切到 ${v.version}，重启 vision 服务后生效`;
            loadModelVersions();
          } catch (err) {
            alert("切换失败：" + err.message);
            btn.disabled = false;
          }
        });
        act.append(btn);
      }
      tr.append(name, st, m, when, act);
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
    let start = (location.hash || "#overview").slice(1);
    if (allowedViews && !allowedViews.has(start)) {
      start = [...allowedViews][0] || "overview";
    }
    go(start);
    ping();
    setInterval(ping, 30000);
  })();
})();
