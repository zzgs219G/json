// 自动导入隔壁的纯 HTML 视图资产
import htmlTemplate from './index.html';

// 在全局作用域缓存一份扫描到的真实文件路径树，避免重复请求暴露或降速
let globalTreeCache = null;

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    // ==========================================
    // 🔒 云端敏感配置区
    // ==========================================
    const AUTH_KEY = env.SECRET_KEY || "614118"; // 你的操作验证密码
    const GITHUB_OWNER = "zzgs219G"; 
    const GITHUB_REPO = "json"; 
    const GITHUB_BRANCH = "main"; 
    const GH_TOKEN = env.GH_TOKEN || ""; // 私有仓库填Token，公开仓库可不填
    const BASE_URL = "https://json.614118.xyz";

    // ==========================================
    // 🌲 核心黑魔法：内部拉取并解析 GitHub 目录树
    // ==========================================
    async function getCachedTree() {
      if (globalTreeCache) return globalTreeCache;
      try {
        const ghApiUrl = `https://api.github.com/repos/${GITHUB_OWNER}/${GITHUB_REPO}/git/trees/${GITHUB_BRANCH}?recursive=1`;
        const headers = { "User-Agent": "Cloudflare-Worker-AutoIndex" };
        if (GH_TOKEN) { headers["Authorization"] = `token ${GH_TOKEN}`; }

        const ghResponse = await fetch(ghApiUrl, { headers });
        if (ghResponse.ok) {
          const treeData = await ghResponse.json();
          // 只抓取真正的文件，且后缀必须是 .json 或者是 .enc
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
    // 🛡️ 路由 1：云端代理匿名测速（彻底防止前端抓包暴露明文）
    // ==========================================
    if (url.pathname === "/api/ping") {
      const id = parseInt(url.searchParams.get("id"));
      const tree = await getCachedTree();
      const targetFile = tree[id];

      if (!targetFile) return new Response("Error", { status: 404 });

      const fullRealUrl = `${BASE_URL}/${targetFile.path}`;
      const startTime = performance.now();
      try {
        // 在云端（服务器内部）发起测速包，外界 F12 网络面板只能看到请求了 Worker，完全看不见这个真实 URL！
        await fetch(fullRealUrl, { method: 'HEAD', cache: 'no-store' });
        const latency = Math.round(performance.now() - startTime);
        return new Response(JSON.stringify({ success: true, latency }), {
          headers: { "Content-Type": "application/json" }
        });
      } catch (e) {
        return new Response(JSON.stringify({ success: false }), { status: 500 });
      }
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
    // 🚀 路由 3：主页面渲染下发（脱敏分发）
    // ==========================================
    // 每次进入页面刷新清空缓存，确保能感知仓库最新文件变动
    globalTreeCache = null; 
    const tree = await getCachedTree();

    // 💡 重点：发给前端的数据结构干净得像张白纸，没有包含任何 URL 路径，只有名字
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
