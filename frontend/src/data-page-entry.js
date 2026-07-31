(() => {
  const entryScript = document.currentScript;
  const modulePath = String(entryScript?.dataset.module || "").trim();
  const pagePath = String(entryScript?.dataset.page || "index.html").trim();
  const localOrigin = "http://127.0.0.1:5173";

  if (!modulePath) {
    throw new Error("数据页面入口缺少 data-module");
  }

  if (window.location.protocol !== "file:") {
    const moduleScript = document.createElement("script");
    moduleScript.type = "module";
    moduleScript.src = modulePath;
    document.head.append(moduleScript);
    return;
  }

  const targetUrl = new URL(pagePath, `${localOrigin}/`).href;
  let settled = false;

  function showFileProtocolGuard() {
    if (settled) return;
    settled = true;

    const mount = () => {
      const style = document.createElement("style");
      style.textContent = `
        body {
          margin: 0;
          min-height: 100vh;
          display: grid;
          place-items: center;
          background: #090d14;
          color: #f4f7fb;
          font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        }
        .fileProtocolGuard {
          width: min(620px, calc(100vw - 40px));
          box-sizing: border-box;
          padding: 28px;
          border: 1px solid #273244;
          border-radius: 14px;
          background: #111824;
          box-shadow: 0 18px 60px rgba(0, 0, 0, 0.35);
        }
        .fileProtocolGuard h1 {
          margin: 0 0 12px;
          font-size: clamp(22px, 4vw, 30px);
        }
        .fileProtocolGuard p {
          margin: 0 0 18px;
          color: #c5cedb;
          line-height: 1.65;
        }
        .fileProtocolGuard a {
          display: inline-flex;
          padding: 10px 16px;
          border-radius: 8px;
          background: #f3f6fa;
          color: #101722;
          font-weight: 700;
          text-decoration: none;
        }
        .fileProtocolGuard code {
          display: block;
          margin-top: 20px;
          padding: 12px;
          overflow-wrap: anywhere;
          border-radius: 8px;
          background: #080c12;
          color: #d8e2ee;
          line-height: 1.6;
        }
      `;

      const panel = document.createElement("main");
      panel.className = "fileProtocolGuard";
      panel.setAttribute("role", "alert");

      const title = document.createElement("h1");
      title.textContent = "需要通过本地服务打开";

      const explanation = document.createElement("p");
      explanation.textContent = "报告由静态 JSON 驱动，浏览器不能从 file:// 源码页安全读取这些数据。";

      const link = document.createElement("a");
      link.href = targetUrl;
      link.textContent = "打开可用页面";

      const command = document.createElement("code");
      command.textContent = "cd frontend\nnpm run build\nnpm run dev";

      panel.append(title, explanation, link, command);
      document.head.append(style);
      document.body.replaceChildren(panel);
      document.title = `请使用本地服务 · ${document.title}`;
    };

    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", mount, { once: true });
    } else {
      mount();
    }
  }

  const probe = new Image();
  const timeout = window.setTimeout(showFileProtocolGuard, 1200);

  probe.addEventListener("load", () => {
    if (settled) return;
    settled = true;
    window.clearTimeout(timeout);
    window.location.replace(targetUrl);
  }, { once: true });
  probe.addEventListener("error", () => {
    window.clearTimeout(timeout);
    showFileProtocolGuard();
  }, { once: true });
  probe.src = `${localOrigin}/f1tr-icon.svg?runtime-probe=${Date.now()}`;
})();
