/* SPM 识别：选图 → 预览 → 出结果。没有工作台，没有多余控件。 */
(() => {
  const $ = (id) => document.getElementById(id);
  const drop = $("drop"), input = $("files"), run = $("run"), out = $("out"),
        dot = $("dot"), hint = $("hint"), preview = $("preview"), tabs = $("tabs");

  const CATS = {
    arrival:    { path: "/v1/arrival",    hint: "点击或拖入接车照片",       name: "接车照片" },
    repair:     { path: "/v1/repair",     hint: "点击或拖入故障及修复照片", name: "故障及修复" },
    inspection: { path: "/v1/inspection", hint: "点击或拖入项目检查表照片", name: "项目检查表" },
  };

  let cat = "arrival";
  let files = [];
  let urls = [];
  let scenes = [];   // 每张图识别到的场景（或 'outside'）

  const el = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  };

  const stat = (label, value, opts = {}) => {
    const box = el("div", "stat");
    box.append(el("div", "k", label));
    const v = el("div", "v", value == null || value === "" ? "—" : String(value));
    if (opts.small) v.classList.add("sm");
    if (opts.tone) v.classList.add(opts.tone);
    box.append(v);
    return box;
  };

  /* ---- 预览 ---- */
  function drawPreview() {
    urls.forEach((u) => URL.revokeObjectURL(u));
    urls = [];
    preview.replaceChildren();
    files.forEach((f, i) => {
      const url = URL.createObjectURL(f);
      urls.push(url);
      const a = el("a");
      a.href = url;
      a.target = "_blank";
      a.rel = "noopener";
      const img = el("img");
      img.src = url;
      img.alt = f.name;
      a.append(img);
      // 识别结果直接贴在缩略图下面：看得出这张被认成了什么，
      // 而不是只看到一个"复核"却不知道哪张出了问题。
      const got = scenes[i];
      const cap = el("span", "cap" + (got ? (got === "outside" ? " bad" : " ok") : ""),
                     got ? (got === "outside" ? "不在本类" : (SCENE[got] || got)) : "");
      const wrap = el("div", "shot");
      wrap.append(a, cap);
      preview.append(wrap);
    });
  }

  function setFiles(list) {
    files = Array.from(list).filter((f) => f.type.startsWith("image/"));
    scenes = [];
    input.value = "";
    drawPreview();
    run.disabled = files.length === 0;
    out.replaceChildren();
  }

  input.addEventListener("change", (e) => setFiles(e.target.files));
  ["dragenter", "dragover"].forEach((t) =>
    drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach((t) =>
    drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
  drop.addEventListener("drop", (e) => setFiles(e.dataTransfer.files));

  /* ---- 切换类别 ---- */
  tabs.addEventListener("click", (e) => {
    const btn = e.target.closest(".tab");
    if (!btn || btn.classList.contains("on")) return;
    cat = btn.dataset.cat;
    [...tabs.children].forEach((b) => b.classList.toggle("on", b === btn));
    hint.textContent = CATS[cat].hint;
    files = [];
    scenes = [];
    drawPreview();
    run.disabled = true;
    out.replaceChildren();
  });

  /* ---- 结果 ---- */
  function renderArrival(d) {
    const s = el("div", "stats");
    s.append(stat("车牌", d.plate, { small: true }));
    s.append(stat("里程", d.odometer != null ? d.odometer + " km" : null));
    s.append(stat("VIN", d.vin.match ? "一致"
      : (d.vin.license || d.vin.frame ? "不一致" : null),
      { tone: d.vin.match ? "ok" : "warn", small: true }));
    return s;
  }
  function renderRepair(d) {
    const photos = d.photos || [];
    const shot = (prefixes) => photos.some((p) => prefixes.some((x) => (p.scene || "").startsWith(x)));
    // 拍了但判不出来 → 待判定；压根没拍 → 未拍。空白"—"分不清这两种，会让人以为系统坏了。
    const cell = (label, value, tone, took) => {
      if (value) return stat(label, value, { tone });
      return stat(label, took ? "待判定" : "未拍", { small: true, tone: took ? "warn" : undefined });
    };
    const s = el("div", "stats");
    s.append(cell("桥", d.oil.axle_label, "ok", shot(["bridge_drain", "bridge_fill", "axle_oil"])));
    s.append(cell("轮胎", d.oil.tire_label, "ok", shot(["tire_drain", "tire_fill", "tire_oil"])));
    s.append(cell("滤芯", d.filter.label, "ok", shot(["filter_has", "filter_none", "filter"])));
    return s;
  }
  function renderInspection(d) {
    const s = el("div", "stats");
    s.append(stat("签字", d.signature ? "已检" : "未检", { tone: d.signature ? "ok" : "warn", small: true }));
    s.append(stat("盖章", d.stamp ? "已检" : "未检", { tone: d.stamp ? "ok" : "warn", small: true }));
    s.append(stat("首页 VIN", d.service_front_vin || d.service_front_vin_partial, { small: true }));
    return s;
  }
  const VIEW = { arrival: renderArrival, repair: renderRepair, inspection: renderInspection };

  function render(d) {
    const wrap = el("div", "result");
    const head = el("div", "head");
    head.append(
      el("div", "name", `${CATS[cat].name} · ${d.photos.length} 张`),
      el("span", "badge " + (d.ok ? "ok" : "warn"), d.ok ? "通过" : "复核"),
    );
    const seen = (d.photos || []).map((p) => SCENE[p.scene] || p.scene).filter(Boolean);
    const uniq = [...new Set(seen)];
    if (uniq.length) wrap.append(el("div", "seen", "识别为：" + uniq.join("、")));
    wrap.append(VIEW[cat](d));

    if (d.reasons?.length) {
      const ul = el("ul", "tips");
      d.reasons.forEach((r) => ul.append(el("li", null, r)));
      wrap.append(ul);
    }
    return wrap;
  }

  /* ---- 提交 ---- */
  run.addEventListener("click", async () => {
    if (!files.length) return;
    run.disabled = true;
    out.replaceChildren(el("div", "msg", "识别中…"));

    const fd = new FormData();
    files.forEach((f) => fd.append("images", f));

    try {
      const res = await fetch(CATS[cat].path, { method: "POST", body: fd });
      const body = await res.json();
      if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`);
      // 按 index 把每张图的识别结果贴回缩略图
      scenes = files.map(() => null);
      const outside = new Set((body.unexpected || []).map((p) => p.index));
      [...(body.photos || []), ...(body.unexpected || [])].forEach((p) => {
        if (typeof p.index === "number") {
          scenes[p.index] = outside.has(p.index) ? "outside" : (p.scene || null);
        }
      });
      drawPreview();
      out.replaceChildren(render(body));
    } catch (err) {
      out.replaceChildren(el("div", "msg bad", "失败：" + err.message));
    } finally {
      run.disabled = false;
    }
  });

  async function ping() {
    try {
      const ok = (await (await fetch("/readyz", { cache: "no-store" })).json()).ok;
      dot.className = "dot " + (ok ? "ok" : "bad");
    } catch { dot.className = "dot bad"; }
  }

  ping();
  setInterval(ping, 30000);
})();
