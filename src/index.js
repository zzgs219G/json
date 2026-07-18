// 自动导入隔壁的纯 HTML 视图资产
import htmlTemplate from './index.html';

let globalTreeCache = null;

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    // ==========================================
    // 🔒 云端安全配置区
    // ==========================================
    const AUTH_KEY = env.SECRET_KEY || "614118"; 
    const GITHUB_OWNER = "zzgs219G"; 
    const GITHUB_REPO = "json"; 
    const GITHUB_BRANCH = "main"; 
    const GH_TOKEN = env.GH_TOKEN || ""; 
    const BASE_URL = "https://json.614118.xyz";

    async function getCachedTree() {
      if (globalTreeCache) return globalTreeCache;
      try {
        const ghApiUrl = `https://api.github.com/repos/${GITHUB_OWNER}/${GITHUB_REPO}/git/trees/${GITHUB_BRANCH}?recursive=1`;
        const headers = { "User-Agent": "Cloudflare-Worker-AutoIndex" };
        if (GH_TOKEN) { headers["Authorization"] = `token ${GH_TOKEN}`; }

        const ghResponse = await fetch(ghApiUrl, { headers });
        if (ghResponse.ok) {
          const treeData = await ghResponse.json();
          globalTreeCache = treeData.tree.filter(node => 
            node.type === "blob" && 
            (node.path.endsWith(".json") || node.path.endsWith(".enc"))
          );
          return globalTreeCache;
        }
      } catch (e) {}
      return [];
    }

    // ==========================================
    // ⚡ 路由 1：【全新重构】真·隐藏路径单独测速中转站
    // ==========================================
    if (url.pathname === "/api/ping") {
      const id = parseInt(url.searchParams.get("id"));
      const tree = await getCachedTree();
      const targetFile = tree[id];

      if (!targetFile) return new Response("Not Found", { status: 404 });

      try {
        const fullRealUrl = `${BASE_URL}/${targetFile.path}`;
        // Worker 在云端和 GitHub 真实握手，配合前端掐表，算出真实的端到端全链路时间
        await fetch(fullRealUrl, { 
          method: 'HEAD', 
          cache: 'no-store',
          signal: AbortSignal.timeout(3000) 
        });
        return new Response(JSON.stringify({ success: true }), {
          headers: { "Content-Type": "application/json" }
        });
      } catch (e) {
        return new Response(JSON.stringify({ success: false }), { status: 500 });
      }
    }

    // ==========================================
    // 🔑 路由 2：安全解锁提取真实明文 URL
    // ==========================================
    if (url.pathname === "/api/get-secure-link") {
      const id = parseInt(url.searchParams.get("id"));
      const key = url.searchParams.get("key");
      const tree = await getCachedTree();
      const targetFile = tree[id];

      if (key === AUTH_KEY && targetFile) {
        return new Response(JSON.stringify({ success: true, url: `${BASE_URL}/${targetFile.path}` }), {
          headers: { "Content-Type": "application/json" }
        });
      }
      return new Response(JSON.stringify({ success: false, msg: "认证失败" }), { 
        status: 403, headers: { "Content-Type": "application/json" } 
      });
    }

    // ==========================================
    // 🔹 路由 3：只在访问首页根路径时，拦截下发导航面板 UI
    // ==========================================
    if (url.pathname === "/" || url.pathname === "/index.html") {
      globalTreeCache = null; 
      const tree = await getCachedTree();

      const publicMetadata = tree.map((file, index) => {
        const pathSegments = file.path.split('/');
        const filename = pathSegments.pop();
        const ext = filename.split('.').pop().toLowerCase();
        const pathInfo = pathSegments.slice(-2).join('/') || 'root';
        return { id: index, filename, ext, pathInfo };
      });

      const rawHtmlString = typeof htmlTemplate === 'string' ? htmlTemplate : htmlTemplate.default;
      if (!rawHtmlString) return new Response("HTML 视图加载失败", { status: 500 });

      const finalHtml = rawHtmlString.replace('/*SERVER_DATA*/ []', JSON.stringify(publicMetadata));
      return new Response(finalHtml, { headers: { "Content-Type": "text/html; charset=utf-8" } });
    }

    // ==========================================
    // 🛡️ 路由 4：反向代理透传。APP请求配置直达 GitHub Raw，彻底隔绝死循环
    // ==========================================
    const githubRawUrl = `https://raw.githubusercontent.com/${GITHUB_OWNER}/${GITHUB_REPO}/${GITHUB_BRANCH}${url.pathname}`;
    const proxyHeaders = new Headers(request.headers);
    if (GH_TOKEN) { proxyHeaders.set("Authorization", `token ${GH_TOKEN}`); }

    try {
      const gitHubResponse = await fetch(githubRawUrl, {
        method: request.method,
        headers: proxyHeaders,
        redirect: "follow"
      });
      if (gitHubResponse.ok) return gitHubResponse;
    } catch (err) {}

    return new Response("资源未在仓库中找到", { status: 404 });
  }
};
