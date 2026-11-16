/* DEEPWAY 统一 SSO —— 前端接入层（原生 JS 版）

对应 skill 的 Step 2 / Step 3：
  - tokenInit()：URL 里的 ?dw-open-token= 落成根域 Cookie，并从地址栏抹掉
  - getToken()：URL → Cookie 双来源，数组归一化
  - 请求头三件套：authorization / appCode / poolCode
  - 401 / 登录失效码 → 统一登出，且做去重（避免并发请求触发多次跳转）
  - 403 / 无权限码 → 提示，不登出
*/
(function () {
  const CFG = {
    enabled: false, mode: "off", openLoginDomain: "", appCode: "", poolCode: "",
    tokenKey: "dw-open-token", views: {},
  };
  const LOGIN_ERROR_CODES = new Set(["A0202", "A0203", "A0220", "A0223", "A0230"]);
  const DENY_ERROR_CODES = new Set(["A0400", "A0301"]);
  let leaving = false;   // 登出去重：并发请求只跳一次

  /* ---------- Cookie ---------- */
  function rootDomain() {
    // localhost / IP 不能写 domain 属性（浏览器会拒绝整条 Cookie），
    // 契约里也提醒过 127.0.0.1 会被 getDomain() 解析成 "0.1"。
    const host = location.hostname;
    if (host === "localhost" || /^\d+(\.\d+){3}$/.test(host)) return "";
    const parts = host.split(".");
    return parts.length >= 2 ? "." + parts.slice(-2).join(".") : "";
  }

  const domainAttr = () => (rootDomain() ? `; domain=${rootDomain()}` : "");

  function readCookie(name) {
    const m = document.cookie.match(new RegExp("(^|;\\s*)" + name + "=([^;]*)"));
    return m ? decodeURIComponent(m[2]) : "";
  }

  function writeCookie(name, value) {
    // 与 open-login-utils 一致：session Cookie，不设 expires
    document.cookie = `${name}=${encodeURIComponent(value)}; path=/${domainAttr()}; SameSite=Lax`;
  }

  function clearCookie(name) {
    document.cookie = `${name}=; path=/${domainAttr()}; max-age=0; SameSite=Lax`;
  }

  /* ---------- Token ---------- */
  function fromUrl() {
    const q = new URLSearchParams(location.search);
    let t = q.get("dw-open-token");
    if (!t && location.hash.includes("dw-open-token=")) {
      const h = new URLSearchParams(location.hash.replace(/^#/, "").split("?")[1] || "");
      t = h.get("dw-open-token");
    }
    return t || "";
  }

  function tokenInit() {
    const t = fromUrl();
    if (!t) return;
    writeCookie(CFG.tokenKey, t);
    // 清理地址栏，避免 Token 留在历史记录 / 被复制外传
    const url = new URL(location.href);
    url.searchParams.delete("dw-open-token");
    if (url.hash.includes("dw-open-token=")) {
      const [path, query] = url.hash.replace(/^#/, "").split("?");
      const hp = new URLSearchParams(query || "");
      hp.delete("dw-open-token");
      url.hash = "#" + path + (hp.toString() ? "?" + hp.toString() : "");
    }
    history.replaceState(null, "", url.toString());
  }

  function getToken() {
    return fromUrl() || readCookie(CFG.tokenKey) || "";
  }

  /* ---------- 登出 ---------- */
  function logout(reason) {
    if (leaving) return;
    if (!CFG.enabled) return;
    leaving = true;
    clearCookie(CFG.tokenKey);
    if (!CFG.openLoginDomain) {
      document.body.innerHTML =
        '<div style="padding:40px;font:14px system-ui">未配置 openLoginDomain，无法跳转登录。'
        + "请检查 SSO_OPEN_LOGIN_DOMAIN。</div>";
      return;
    }
    const redirect = encodeURIComponent(location.href);
    const url = `${CFG.openLoginDomain.replace(/\/$/, "")}/open/login/?poolCode=${encodeURIComponent(CFG.poolCode)}`
      + `&appCode=${encodeURIComponent(CFG.appCode)}&redirect=${redirect}`;
    if (reason) sessionStorage.setItem("sso_reason", reason);
    location.replace(url);
  }

  /* ---------- 请求封装 ---------- */
  function authFetch(input, init) {
    const opts = Object.assign({}, init || {});
    const headers = new Headers(opts.headers || {});
    const token = getToken();
    if (token) headers.set("authorization", token);   // 原值，不加 Bearer
    if (CFG.appCode) headers.set("appCode", CFG.appCode);
    if (CFG.poolCode) headers.set("poolCode", CFG.poolCode);
    opts.headers = headers;
    opts.credentials = "include";

    return fetch(input, opts).then(async (res) => {
      if (res.status === 401) {
        logout("登录已失效，请重新登录");
        throw new Error("登录已失效");
      }
      if (res.status === 403) {
        const body = await res.clone().json().catch(() => ({}));
        throw Object.assign(new Error("无权限"), { deny: true, body });
      }
      return res;
    });
  }

  /* ---------- 启动 ---------- */
  async function boot() {
    try {
      const r = await fetch("/sso/config", { cache: "no-store" });
      Object.assign(CFG, await r.json());
    } catch (e) {
      CFG.enabled = false;   // 拿不到配置就按不鉴权跑，避免白屏
    }
    if (!CFG.enabled) return { enabled: false, menus: null, user: null };

    tokenInit();
    if (!getToken()) {
      logout();
      return new Promise(() => {});   // 正在跳转，停在这里
    }
    const me = await authFetch("/sso/me").then((r) => r.json());
    return me;
  }

  window.SSO = { boot, authFetch, logout, getToken, tokenInit, CFG };
})();
