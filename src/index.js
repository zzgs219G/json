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

    const edgeCache = caches.default;

    async function getTreeWithCache(forceRefresh = false) {
      const cacheUrl = new URL(`${BASE_URL}/internal-cache/github-tree`);
      if (forceRefresh) {
        await edgeCache.delete(cacheUrl);
      } else {
        const cachedResponse = await edgeCache.match(cacheUrl);
        if (cachedResponse) return await cachedResponse.json();
      }

      try {
        const ghApiUrl = `https://api.github.com/repos/${GITHUB_OWNER}/${GITHUB_REPO}/git/trees/${GITHUB_BRANCH}?recursive=1`;
        const headers = { "User-Agent": "Cloudflare-Worker-Secure-Router" };
        if (GH_TOKEN) { headers["Authorization"] = `token ${GH_TOKEN}`; }

        const ghResponse = await fetch(ghApiUrl, { headers });
        if (ghResponse.ok) {
          const treeData = await ghResponse.json();
          const filteredTree = treeData.tree.filter(node => 
            node.type === "blob" && 
            (node.path.endsWith(".json") || node.path.endsWith(".enc"))
          );

          const cacheResponse = new Response(JSON.stringify(filteredTree), {
            headers: { "Cache-Control": "public, max-age=43200" }
          });
          await edgeCache.put(cacheUrl, cacheResponse);
          return filteredTree;
        }
      } catch (e) {}
      return [];
    }

    // ==========================================
    // ♻️ 路由 1：清空缓存，手动同步仓库
    // ==========================================
    if (url.pathname === "/api/flush-cache") {
      const key = url.searchParams.get("key");
      if (key !== AUTH_KEY) return new Response("Unauthorized", { status: 401 });
      await getTreeWithCache(true);
      return new Response(JSON.stringify({ success: true }), {
        headers: { "Content-Type": "application/json" }
      });
    }

    // ==========================================
    // ⚡ 路由 2：全隐匿端到端网络测速中转站
    // ==========================================
    if (url.pathname === "/api/ping") {
      const id = parseInt(url.searchParams.get("id"));
      const tree = await getTreeWithCache();
      const targetFile = tree[id];

      if (!targetFile) return new Response("Not Found", { status: 404 });

      try {
        const fullRealUrl = `${BASE_URL}/${targetFile.path}`;
        await fetch(fullRealUrl, { method: 'HEAD', cache: 'no-store' });
        return new Response(JSON.stringify({ success: true }), {
          headers: { "Content-Type": "application/json" }
        });
      } catch (e) {
        return new Response(JSON.stringify({ success: false }), { status: 500 });
      }
    }

    // ==========================================
    // 🔑 路由 3：安全锁提取真实明文 URL
    // ==========================================
    if (url.pathname === "/api/get-secure-link") {
      const id = parseInt(url.searchParams.get("id"));
      const key = url.searchParams.get("key");
      const tree = await getTreeWithCache();
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
    // 🔹 路由 4：访问首页根路径下发导航面板 UI
    // ==========================================
    if (url.pathname === "/" || url.pathname === "/index.html") {
      const tree = await getTreeWithCache();

      // 🛠️ 核心修正：publicMetadata 干净得像张白纸，彻底抹除 pathInfo，外人无法再拼凑明文路径
      const publicMetadata = tree.map((file, index) => {
        const filename = file.path.split('/').pop();
        const ext = filename.split('.').pop().toLowerCase();
        return { id: index, filename, ext };
      });

      const rawHtmlString = typeof htmlTemplate === 'string' ? htmlTemplate : htmlTemplate.default;
      if (!rawHtmlString) return new Response("HTML 加载失败", { status: 500 });

      const finalHtml = rawHtmlString.replace('/*SERVER_DATA*/ []', JSON.stringify(publicMetadata));
      return new Response(finalHtml, { headers: { "Content-Type": "text/html; charset=utf-8" } });
    }

    // ==========================================
    // 🛡️ 路由 5：反向代理透传（带静态边缘缓存，APP访问专用通道）
    // ==========================================
    const cacheKeyRequest = new Request(request.url, { method: "GET", headers: request.headers });
    let cachedAsset = await edgeCache.match(cacheKeyRequest);
    if (cachedAsset) return cachedAsset; 

    const githubRawUrl = `https://raw.githubusercontent.com/${GITHUB_OWNER}/${GITHUB_REPO}/${GITHUB_BRANCH}${url.pathname}`;
    const proxyHeaders = new Headers(request.headers);
    if (GH_TOKEN) { proxyHeaders.set("Authorization", `token ${GH_TOKEN}`); }

    try {
      const gitHubResponse = await fetch(githubRawUrl, {
        method: request.method,
        headers: proxyHeaders,
        redirect: "follow"
      });

      if (gitHubResponse.ok) {
        const newResponse = new Response(gitHubResponse.body, gitHubResponse);
        newResponse.headers.set("Cache-Control", "public, max-age=7200");
        newResponse.headers.set("Access-Control-Allow-Origin", "*");
        if (request.method === "GET") {
          await edgeCache.put(cacheKeyRequest, newResponse.clone());
        }
        return newResponse;
      }
    } catch (err) {}

    return new Response("Asset Not Found In Config Repo", { status: 404 });
  }
};
