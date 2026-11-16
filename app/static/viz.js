/* SPM 数据看板：数据集浏览（带检测框） / 评测 / 训练曲线。只读。 */
(() => {
  const $ = (id) => document.getElementById(id);
  const el = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  };
  const api = (p) => fetch(p, { cache: "no-store" }).then((r) => {
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return r.json();
  });

  /* ---------- 视图切换 ---------- */
  $("tabs").addEventListener("click", (e) => {
    const b = e.target.closest(".tab");
    if (!b) return;
    [...$("tabs").children].forEach((x) => x.classList.toggle("on", x === b));
    ["dataset", "eval", "train", "mistakes"].forEach((v) => {
      $("view-" + v).hidden = v !== b.dataset.view;
    });
    if (b.dataset.view === "eval") loadEval();
    if (b.dataset.view === "train") loadTrain();
    if (b.dataset.view === "mistakes") loadMistakes();
  });

  /* ---------- 数据集 ---------- */
  let current = null;          // 当前类别目录
  let cache = new Map();       // path -> boxes

  async function loadRoots() {
    const d = await api("/viz/api/classes");
    const sel = $("root");
    sel.replaceChildren();
    (d.roots || []).forEach((r) => {
      const o = el("option", null, r.split("/").slice(-1)[0]);
      o.value = r;
      if (r === d.root) o.selected = true;
      sel.append(o);
    });
    renderClasses(d.classes);
  }

  function renderClasses(classes) {
    const box = $("classes");
    box.replaceChildren();
    classes.forEach((c) => {
      const row = el("div", "cls");
      row.append(el("span", null, c.name), el("span", "n", String(c.count)));
      row.dataset.path = c.path;
      row.dataset.name = c.name;
      row.addEventListener("click", () => selectClass(row, c));
      box.append(row);
    });
  }

  async function selectClass(row, c) {
    [...$("classes").children].forEach((x) => x.classList.toggle("on", x === row));
    current = c;
    $("clsTitle").textContent = `${c.name} · ${c.count} 张`;
    $("detail").hidden = true;
    const d = await api(`/viz/api/images?dir=${encodeURIComponent(c.path)}`);
    const grid = $("grid");
    grid.replaceChildren();
    d.files.forEach((p) => {
      const t = el("div", "thumb");
      const img = el("img");
      img.loading = "lazy";
      img.src = `/viz/api/file?path=${encodeURIComponent(p)}`;
      t.append(img);
      t.addEventListener("click", () => openDetail(p));
      grid.append(t);
    });
  }

  function paintBoxes(container, boxes, imgEl) {
    container.replaceChildren();
    if (!boxes.length) return;
    const { naturalWidth: nw, naturalHeight: nh } = imgEl;
    if (!nw || !nh) return;
    const rect = imgEl.getBoundingClientRect();
    const sx = rect.width / nw;
    const sy = rect.height / nh;
    boxes.forEach((b) => {
      const [x1, y1, x2, y2] = b.box;
      const d = el("div", "bx");
      d.style.left = `${x1 * sx}px`;
      d.style.top = `${y1 * sy}px`;
      d.style.width = `${(x2 - x1) * sx}px`;
      d.style.height = `${(y2 - y1) * sy}px`;
      d.append(el("span", null, `${b.label} ${b.score.toFixed(2)}`));
      container.append(d);
    });
  }

  async function openDetail(path) {
    $("detail").hidden = false;
    $("detailName").textContent = path.split("/").slice(-1)[0];
    const img = $("detailImg");
    img.src = `/viz/api/file?path=${encodeURIComponent(path)}`;
    $("detailBoxes").replaceChildren();
    $("detailMeta").textContent = "检测中…";

    await new Promise((r) => { img.onload = r; if (img.complete) r(); });

    if (!$("showBoxes").checked) {
      $("detailMeta").textContent = "已关闭框显示";
      return;
    }
    let boxes = cache.get(path);
    if (!boxes) {
      try {
        boxes = (await api(`/viz/api/detect?path=${encodeURIComponent(path)}`)).boxes;
      } catch {
        boxes = [];
      }
      cache.set(path, boxes);
    }
    paintBoxes($("detailBoxes"), boxes, img);
    $("detailMeta").textContent = boxes.length
      ? `${boxes.length} 个框：` + [...new Set(boxes.map((b) => b.label))].join("、")
      : "未检测到目标（这一类可能不是检测器覆盖的类别）";
  }

  $("detailClose").addEventListener("click", () => { $("detail").hidden = true; });
  $("showBoxes").addEventListener("change", () => {
    const p = $("detailImg").src;
    if (!p) return;
    if (!$("showBoxes").checked) {
      $("detailBoxes").replaceChildren();
      $("detailMeta").textContent = "已关闭框显示";
    } else {
      openDetail(decodeURIComponent(p.split("path=")[1]));
    }
  });
  $("root").addEventListener("change", (e) => {
    $("classes").replaceChildren();
    $("grid").replaceChildren();
    $("detail").hidden = true;
    api(`/viz/api/classes?root=${encodeURIComponent(e.target.value)}`)
      .then((d) => renderClasses(d.classes));
  });

  /* ---------- 评测 ---------- */
  function accBar(v) {
    const w = Math.round((v || 0) * 100);
    const wrap = el("div", "bar-cell");
    const i = el("i");
    i.style.width = `${w}%`;
    if (v != null && v < 0.7) i.classList.add("low");
    wrap.append(i);
    return wrap;
  }

  function table(rows, cols) {
    const t = el("table", "tbl");
    const tr = el("tr");
    cols.forEach((c) => tr.append(el("th", null, c.title)));
    const thead = el("thead");
    thead.append(tr);
    t.append(thead);
    const tb = el("tbody");
    rows.forEach((r) => {
      const row = el("tr");
      cols.forEach((c) => row.append(c.cell(r)));
      tb.append(row);
    });
    t.append(tb);
    return t;
  }

  async function loadEval() {
    const body = $("evalBody");
    body.replaceChildren(el("div", "meta", "加载中…"));
    try {
      const d = await api("/viz/api/eval");
      body.replaceChildren();

      if (d.folders_new) {
        body.append(el("h3", "sect", `新数据逐类（${d.folders_new.total} 张，总体 ${(d.folders_new.accuracy * 100).toFixed(1)}%）`));
        const oldMap = {};
        (d.folders_old?.by_folder || []).forEach((s) => { oldMap[s.folder] = s.accuracy; });
        body.append(table(d.folders_new.by_folder, [
          { title: "目录", cell: (r) => el("td", null, r.folder) },
          { title: "张数", cell: (r) => el("td", "num", String(r.n)) },
          { title: "新数据准确率", cell: (r) => { const td = el("td", "num"); td.append(document.createTextNode(`${(r.accuracy * 100).toFixed(0)}% `), accBar(r.accuracy)); return td; } },
          { title: "老数据", cell: (r) => el("td", "num", oldMap[r.folder] != null ? `${(oldMap[r.folder] * 100).toFixed(0)}%` : "—") },
          { title: "主要误判", cell: (r) => el("td", "num", (r.confusions || []).slice(0, 2).map(([k, v]) => `${k}×${v}`).join("、") || "—") },
        ]));
      }

      if (d.fivefold) {
        body.append(el("h3", "sect", `5 折交叉验证（top1 均值 ${(d.fivefold.top1_mean * 100).toFixed(2)}% ± ${(d.fivefold.top1_std * 100).toFixed(2)}）`));
        body.append(table(d.fivefold.folds, [
          { title: "折", cell: (r) => el("td", null, String(r.fold)) },
          { title: "验证张数", cell: (r) => el("td", "num", String(r.n_val)) },
          { title: "top1", cell: (r) => { const td = el("td", "num"); td.append(document.createTextNode(`${(r.top1 * 100).toFixed(2)}% `), accBar(r.top1)); return td; } },
          { title: "top5", cell: (r) => el("td", "num", `${(r.top5 * 100).toFixed(0)}%`) },
          { title: "高置信覆盖", cell: (r) => el("td", "num", `${(r.auto_coverage * 100).toFixed(1)}%`) },
        ]));
      }

      if (d.api_scene) {
        body.append(el("h3", "sect", `接口级场景精度 · fold-${d.api_scene.fold}（${(d.api_scene.accuracy * 100).toFixed(2)}%，${d.api_scene.correct}/${d.api_scene.n}）`));
        const rows = Object.entries(d.api_scene.per_class).map(([k, v]) => ({ name: k, ...v }));
        body.append(table(rows, [
          { title: "类别", cell: (r) => el("td", null, r.name) },
          { title: "张数", cell: (r) => el("td", "num", String(r.n)) },
          { title: "正确", cell: (r) => el("td", "num", String(r.correct)) },
          { title: "准确率", cell: (r) => { const td = el("td", "num"); td.append(document.createTextNode(`${(r.acc * 100).toFixed(0)}% `), accBar(r.acc)); return td; } },
        ]));
      }
    } catch (err) {
      body.replaceChildren(el("div", "meta", "加载失败：" + err.message));
    }
  }

  /* ---------- 训练曲线 ---------- */
  function spark(series, key, color) {
    const vals = series.map((s) => s[key]).filter((v) => v != null);
    if (vals.length < 2) return el("div", "legend", "数据不足");
    const w = 600, h = 160, pad = 6;
    const min = Math.min(...vals), max = Math.max(...vals);
    const span = max - min || 1;
    const pts = vals.map((v, i) => {
      const x = pad + (i / (vals.length - 1)) * (w - pad * 2);
      const y = h - pad - ((v - min) / span) * (h - pad * 2);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    }).join(" ");
    const ns = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(ns, "svg");
    svg.setAttribute("class", "chart");
    svg.setAttribute("viewBox", `0 0 ${w} ${h}`);
    const poly = document.createElementNS(ns, "polyline");
    poly.setAttribute("points", pts);
    poly.setAttribute("fill", "none");
    poly.setAttribute("stroke", color);
    poly.setAttribute("stroke-width", "2");
    svg.append(poly);
    const box = el("div");
    box.append(svg);
    box.append(el("div", "legend",
      `${key}: ${(min * 100).toFixed(1)}% → ${(max * 100).toFixed(1)}%（末值 ${(vals[vals.length - 1] * 100).toFixed(1)}%）`));
    return box;
  }

  async function loadTrain() {
    const body = $("trainBody");
    body.replaceChildren(el("div", "meta", "加载中…"));
    try {
      const d = await api("/viz/api/runs");
      body.replaceChildren();
      if (!d.runs.length) {
        body.append(el("div", "meta", "var/runs 下没有训练记录"));
        return;
      }
      d.runs.forEach((r) => {
        const card = el("div");
        card.append(el("h3", "sect", `${r.name}（${r.series.length} epoch）`));
        card.append(spark(r.series, "top1", "#2563eb"));
        body.append(card);
      });
    } catch (err) {
      body.replaceChildren(el("div", "meta", "加载失败：" + err.message));
    }
  }

  /* ---------- 错误样本工作台 ---------- */
  const SCENE = {
    arrival_plate: "车牌", license_vin: "行驶证", frame_vin: "车架 VIN",
    odometer: "里程表", bridge_drain: "桥放油", bridge_fill: "桥加油",
    tire_drain: "轮胎放油", tire_fill: "轮胎加油",
    filter_none: "无滤芯", filter_has: "有滤芯",
    service_front: "服务单首页", service_sign: "服务单末页",
  };
  const sname = (k) => SCENE[k] || k || "—";

  let mSource = "all";
  let mData = null;
  let mGroup = null;
  let selected = new Set();

  async function loadMistakes() {
    $("mGrid").replaceChildren(el("div", "meta", "加载中…"));
    try {
      mData = await api(`/viz/api/mistakes?source=${mSource}`);
    } catch (err) {
      $("mGrid").replaceChildren(el("div", "meta", "加载失败：" + err.message));
      return;
    }
    renderGroups();
    $("mGrid").replaceChildren();
    $("mTitle").textContent = `共 ${mData.total} 张误判，按「应为 → 实际」分组`;
    $("mSave").disabled = true;
  }

  function renderGroups() {
    const box = $("mGroups");
    box.replaceChildren();
    (mData?.groups || []).forEach((g) => {
      const row = el("div", "cls");
      row.append(el("span", null, `${sname(g.want)} → ${sname(g.got)}`),
                 el("span", "n", String(g.n)));
      row.addEventListener("click", () => {
        [...box.children].forEach((x) => x.classList.toggle("on", x === row));
        openGroup(g);
      });
      box.append(row);
    });
  }

  function openGroup(g) {
    mGroup = g;
    selected = new Set();
    $("mTitle").textContent = `${sname(g.want)} → ${sname(g.got)} · ${g.n} 张 · 平均置信度 ${g.avg_conf}`;
    $("mSave").disabled = true;
    const grid = $("mGrid");
    grid.replaceChildren();
    g.items.forEach((it) => {
      const c = el("div", "mcard");
      c.dataset.path = it.path;
      const img = el("img");
      img.loading = "lazy";
      img.src = `/viz/api/file?path=${encodeURIComponent(it.path)}`;
      const chk = el("input", "chk2");
      chk.type = "checkbox";
      chk.addEventListener("click", (e) => {
        e.stopPropagation();
        if (chk.checked) selected.add(it.path); else selected.delete(it.path);
        c.classList.toggle("sel", chk.checked);
        $("mSave").disabled = selected.size === 0;
      });
      c.append(img, el("span", "badge2", `${(it.conf * 100).toFixed(0)}%`),
               el("span", "guess", `判成 ${sname(it.got)}`), chk);
      c.addEventListener("click", () => openMDetail(it));
      grid.append(c);
    });
  }

  async function openMDetail(it) {
    $("mDetail").hidden = false;
    $("mDetailName").textContent = it.file;
    const img = $("mDetailImg");
    img.src = `/viz/api/file?path=${encodeURIComponent(it.path)}`;
    $("mDetailBoxes").replaceChildren();
    $("mDetailMeta").textContent = "检测中…";
    await new Promise((r) => { img.onload = r; if (img.complete) r(); });
    try {
      const d = await api(`/viz/api/detect?path=${encodeURIComponent(it.path)}`);
      paintBoxes($("mDetailBoxes"), d.boxes, img);
      $("mDetailMeta").textContent =
        `应为 ${sname(it.want)}，判成 ${sname(it.got)}（${(it.conf * 100).toFixed(1)}%）· `
        + `${d.boxes.length} 个检测框`;
    } catch {
      $("mDetailMeta").textContent = `应为 ${sname(it.want)}，判成 ${sname(it.got)}`;
    }
  }

  $("mDetailClose").addEventListener("click", () => { $("mDetail").hidden = true; });
  $("mSelectAll").addEventListener("click", () => {
    if (!mGroup) return;
    const all = selected.size === mGroup.items.length;
    selected = all ? new Set() : new Set(mGroup.items.map((i) => i.path));
    [...$("mGrid").children].forEach((c) => {
      const on = !all;
      c.classList.toggle("sel", on);
      const chk = c.querySelector(".chk2");
      if (chk) chk.checked = on;
    });
    $("mSave").disabled = selected.size === 0;
  });
  $("mSave").addEventListener("click", async () => {
    if (!selected.size || !mGroup) return;
    const items = mGroup.items
      .filter((i) => selected.has(i.path))
      .map((i) => ({ path: i.path, want: i.want, got: i.got }));
    try {
      const r = await fetch("/viz/api/hard_cases", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ items }),
      });
      const d = await r.json();
      $("mSave").textContent = `已登记 ${d.added} 张`;
      setTimeout(() => { $("mSave").textContent = "登记为难例"; }, 1600);
    } catch (err) {
      $("mSave").textContent = "失败：" + err.message;
    }
  });
  $("mSource").addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    [...$("mSource").children].forEach((x) => x.classList.toggle("on", x === b));
    mSource = b.dataset.src;
    loadMistakes();
  });

  loadRoots();
})();
