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
    // ⚡ 路由 1：打包聚合测速接口（只产生 1 次前端到 Worker 的请求）
    // ==========================================
    if (url.pathname === "/api/ping-all") {
      const tree = await getCachedTree();
      
      const testPromises = tree.map(async (targetFile, index) => {
        const fullRealUrl = `${BASE_URL}/${targetFile.path}`;
        const startTime = performance.now();
        try {
          // 发起轻量级 HEAD 探测，3秒强行超时
          await fetch(fullRealUrl, { 
            method: 'HEAD', 
            cache: 'no-store',
            signal: AbortSignal.timeout(3000) 
          });
          return { id: index, success: true, latency: Math.round(performance.now() - startTime) };
        } catch (e) {
          return { id: index, success: false };
        }
      });

      const results = await Promise.all(testPromises);
      return new Response(JSON.stringify(results), {
        headers: { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*" }
      });
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
    // 🔹 路由 3：只在访问根目录时才拦截下发导航页 UI
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
    // 🛡️ 【重大修复】非核心 API/导航路径（如 APP 读取配置路径），原路透传放行给源站！
    // ==========================================
    return fetch(request);
  }
};
