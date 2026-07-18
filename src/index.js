import htmlTemplate from './index.html';

let globalTreeCache = null;

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    // ==========================================
    // 🔒 云端敏感配置区
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
    // 🚀 路由 1：【全新重构】云端并发聚合测速（N个文件只算1次Worker请求）
    // ==========================================
    if (url.pathname === "/api/ping-all") {
      const tree = await getCachedTree();
      
      // 使用 Promise.all 像轰炸机一样并发测速，CF 限制单个事件内子请求上限一般为 50 个
      const testPromises = tree.map(async (targetFile, index) => {
        const fullRealUrl = `${BASE_URL}/${targetFile.path}`;
        const startTime = performance.now();
        try {
          // 3秒强行超时，防止挂死
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
    // 🔑 路由 2：安全解锁解密真实明文 URL
    // ==========================================
    if (url.pathname === "/api/get-secure-link") {
      const id = parseInt(url.searchParams.get("id"));
      const key = url.searchParams.get("key");
      const tree = await getCachedTree();
      const targetFile = tree[id];

      if (key === AUTH_KEY && targetFile) {
        const fullRealUrl = `${BASE_URL}/${targetFile.path}`;
        return new Response(JSON.stringify({ success: true, url: fullRealUrl }), {
          headers: { "Content-Type": "application/json" }
        });
      }
      return new Response(JSON.stringify({ success: false, msg: "认证失败" }), { 
        status: 403, headers: { "Content-Type": "application/json" } 
      });
    }

    // ==========================================
    // 🗂️ 路由 3：页面首屏分发
    // ==========================================
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
};
