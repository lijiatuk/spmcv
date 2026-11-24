/* 控制台：单张识别测试。没有构建步骤，原生 JS。 */
(() => {
  "use strict";

  const SCENE_META = {
    arrival_plate: { label: "车头及车牌号", fields: "plate 车牌" },
    license_vin: { label: "行驶证 VIN", fields: "vin" },
    frame_vin: { label: "车架贴牌 VIN", fields: "vin" },
    odometer: { label: "里程表", fields: "odometer 里程" },
    bridge_drain: { label: "桥放油", fields: "action = drain" },
    bridge_fill: { label: "桥加油", fields: "action = fill" },
    tire_drain: { label: "轮胎放油", fields: "action = drain" },
    tire_fill: { label: "轮胎加油", fields: "action = fill" },
    filter_none: { label: "无滤芯", fields: "filter_state = none" },
    filter_has: { label: "有滤芯", fields: "filter_state = has" },
    service_front: { label: "服务单首页 VIN", fields: "vin / vin_partial" },
    service_sign: { label: "服务单末页（签字+盖章）", fields: "signature / stamp" },
  };

  const $ = (sel) => document.querySelector(sel);
  const el = (tag, attrs = {}, text = "") => {
    const node = document.createElement(tag);
    Object.entries(attrs).forEach(([k, v]) => node.setAttribute(k, v));
    if (text) node.textContent = text;
    return node;
  };

  const state = {
    source: "file",
    file: null,
    objectUrl: null,
    busy: false,
  };

  /* ---------------- 通用 ---------------- */

  function toast(message) {
    const node = $("#toast");
    node.textContent = message;
    node.classList.add("show");
    clearTimeout(toast._t);
    toast._t = setTimeout(() => node.classList.remove("show"), 2200);
  }

  async function api(url, options = {}) {
    const res = await fetch(url, options);
    let body = null;
    try { body = await res.json(); } catch { body = null; }
    return { status: res.status, body };
  }

  function go(view) {
    document.querySelectorAll(".nav-item[data-view]").forEach((a) => {
      a.classList.toggle("on", a.dataset.view === view);
    });
    document.querySelectorAll(".view").forEach((section) => {
      section.hidden = section.id !== `view-${view}`;
    });
    $("#pageTitle").textContent = "识别测试";
    $("#sidebar").classList.remove("open");
  }

  /* ---------------- 识别测试 ---------------- */

  function fillScenes() {
    const select = $("#sceneSelect");
    select.innerHTML = "";
    Object.entries(SCENE_META).forEach(([scene, meta]) => {
      const opt = el("option", { value: scene }, `${scene} · ${meta.label}`);
      select.appendChild(opt);
    });

    const tbody = $("#sceneTable tbody");
    tbody.innerHTML = "";
    Object.entries(SCENE_META).forEach(([scene, meta]) => {
      const tr = el("tr");
      tr.appendChild(el("td", {}, scene));
      tr.appendChild(el("td", {}, meta.label));
      tr.appendChild(el("td", {}, meta.fields));
      tbody.appendChild(tr);
    });
  }

  function setSource(source) {
    state.source = source;
    document.querySelectorAll(".tab").forEach((t) => {
      t.classList.toggle("on", t.dataset.source === source);
    });
    $("#sourceFile").hidden = source !== "file";
    $("#sourceUrl").hidden = source !== "url";
    refreshRunState();
  }

  function refreshRunState() {
    const hasInput = state.source === "file" ? !!state.file : !!$("#urlInput").value.trim();
    $("#runBtn").disabled = state.busy || !hasInput;
  }

  function showPreview(blob, name) {
    if (state.objectUrl) URL.revokeObjectURL(state.objectUrl);
    state.objectUrl = URL.createObjectURL(blob);
    $("#previewImg").src = state.objectUrl;
    $("#preview").hidden = false;
    const kb = Math.round(blob.size / 1024);
    $("#previewMeta").innerHTML = `${name}<br>${kb} KB`;
  }

  function pickFile(file) {
    if (!file) return;
    state.file = file;
    showPreview(file, file.name);
    $("#runHint").textContent = `已选择 ${file.name}，点“开始识别”。`;
    refreshRunState();
  }

  function clearAll() {
    state.file = null;
    $("#fileInput").value = "";
    $("#urlInput").value = "";
    $("#preview").hidden = true;
    if (state.objectUrl) { URL.revokeObjectURL(state.objectUrl); state.objectUrl = null; }
    $("#result").hidden = true;
    $("#resultEmpty").hidden = false;
    $("#runHint").textContent = "选择一张图片后开始识别。";
    refreshRunState();
  }

  function renderResult(status, body, elapsedMs) {
    const ok = status === 200;
    $("#resultEmpty").hidden = true;
    $("#result").hidden = false;

    const verdict = $("#verdict");
    verdict.className = `verdict ${ok ? "ok" : "bad"}`;
    verdict.textContent = ok
      ? "识别成功（HTTP 200）"
      : `识别不成功（HTTP ${status}）：${body?.message || "未知原因"}`;

    const dl = $("#resultData");
    dl.innerHTML = "";
    const data = body?.data || {};
    const keys = Object.keys(data);
    if (!keys.length) {
      dl.appendChild(el("dt", {}, "（无字段）"));
      dl.appendChild(el("dd", {}, "—"));
    }
    keys.forEach((key) => {
      dl.appendChild(el("dt", {}, key));
      const value = data[key];
      dl.appendChild(el("dd", {}, value === null ? "—" : String(value)));
    });

    $("#resultJson").textContent = JSON.stringify(body, null, 2);
    $("#resultTiming").textContent = `耗时 ${elapsedMs} ms`;
  }

  async function run() {
    if (state.busy) return;
    const scene = $("#sceneSelect").value;
    const form = new FormData();
    form.append("scene", scene);

    if (state.source === "file") {
      if (!state.file) return;
      form.append("image", state.file, state.file.name || "image.jpg");
    } else {
      const url = $("#urlInput").value.trim();
      if (!url) return;
      form.append("image_url", url);
    }

    state.busy = true;
    refreshRunState();
    $("#runBtn").textContent = "识别中…";
    $("#runHint").textContent = "正在识别，单张最长 5 秒…";
    const started = performance.now();

    try {
      const { status, body } = await api("/v1/recognize", { method: "POST", body: form });
      const elapsed = Math.round(performance.now() - started);
      if (body) renderResult(status, body, elapsed);
      else renderResult(status, { message: "响应不是 JSON" }, elapsed);
      $("#runHint").textContent = status === 200
        ? "识别完成。" : "识别不成功，可让现场按提示重拍后重试。";
    } catch (err) {
      toast(`请求失败：${err.message}`);
      $("#runHint").textContent = "请求失败，检查网络或服务状态。";
    } finally {
      state.busy = false;
      $("#runBtn").textContent = "开始识别";
      refreshRunState();
    }
  }

  async function loadMeta() {
    const { status, body } = await api("/v1/meta");
    if (status !== 200 || !body) return;
    $("#apiVersion").textContent = `API v${body.version}`;
    const model = body.model || {};
    $("#modelVersion").textContent =
      `模型 ${model.configured_version || "—"} · 并发 ${body.limits?.max_concurrent ?? "—"}`;
  }

  /* ---------------- 事件绑定 ---------------- */

  function bind() {
    document.querySelectorAll(".nav-item[data-view]").forEach((a) => {
      a.addEventListener("click", (e) => { e.preventDefault(); go(a.dataset.view); });
    });

    $("#menuBtn").addEventListener("click", () => {
      const open = $("#sidebar").classList.toggle("open");
      $("#menuBtn").setAttribute("aria-expanded", String(open));
    });

    document.querySelectorAll(".tab").forEach((t) => {
      t.addEventListener("click", () => setSource(t.dataset.source));
    });

    $("#drop").addEventListener("click", () => $("#fileInput").click());
    $("#fileInput").addEventListener("change", (e) => pickFile(e.target.files[0]));
    ["dragenter", "dragover"].forEach((name) => {
      $("#drop").addEventListener(name, (e) => { e.preventDefault(); $("#drop").classList.add("over"); });
    });
    ["dragleave", "drop"].forEach((name) => {
      $("#drop").addEventListener(name, (e) => { e.preventDefault(); $("#drop").classList.remove("over"); });
    });
    $("#drop").addEventListener("drop", (e) => pickFile(e.dataTransfer.files[0]));

    $("#urlInput").addEventListener("input", refreshRunState);
    $("#runBtn").addEventListener("click", run);
    $("#clearBtn").addEventListener("click", clearAll);

    window.addEventListener("hashchange", () => {
      if (location.hash.replace("#", "") === "recognize") go("recognize");
    });
  }

  function boot() {
    fillScenes();
    bind();
    setSource("file");
    loadMeta();
    go("recognize");
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();