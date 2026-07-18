import htmlTemplate from './index.html';

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
      if (forceRefresh) await edgeCache.delete(cacheUrl);
      else {
        const cachedResponse = await edgeCache.match(cacheUrl);
        if (cachedResponse) return await cachedResponse.json();
      }

      try {
        const ghApiUrl = `https://api.github.com/repos/${GITHUB_OWNER}/${GITHUB_REPO}/git/trees/${GITHUB_BRANCH}?recursive=1`;
        const headers = { "User-Agent": "Cloudflare-Worker-Pro-Router" };
        if (GH_TOKEN) headers["Authorization"] = `token ${GH_TOKEN}`; 

        const ghResponse = await fetch(ghApiUrl, { headers });
        if (ghResponse.ok) {
          const treeData = await ghResponse.json();
          const filteredTree = treeData.tree.filter(node => 
            node.type === "blob" && (node.path.endsWith(".json") || node.path.endsWith(".enc"))
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
    // ♻️ 路由 1：手动同步仓库
    // ==========================================
    if (url.pathname === "/api/flush-cache") {
      const key = url.searchParams.get("key");
      if (key !== AUTH_KEY) return new Response("Unauthorized", { status: 401 });
      await getTreeWithCache(true);
      return new Response(JSON.stringify({ success: true }), { headers: { "Content-Type": "application/json" } });
    }

    // ==========================================
    // ⚡ 路由 2：单点网络测速
    // ==========================================
    if (url.pathname === "/api/ping") {
      const id = parseInt(url.searchParams.get("id"));
      const tree = await getTreeWithCache();
      const targetFile = tree[id];
      if (!targetFile) return new Response("Not Found", { status: 404 });
      try {
        await fetch(`${BASE_URL}/${targetFile.path}`, { method: 'HEAD', cache: 'no-store' });
        return new Response(JSON.stringify({ success: true }), { headers: { "Content-Type": "application/json" } });
      } catch (e) {
        return new Response(JSON.stringify({ success: false }), { status: 500 });
      }
    }

    // ==========================================
    // 🔑 路由 3：【重构核心】全局解锁接口 (消耗1次请求，下发所有真实URL和真实路径)
    // ==========================================
    if (url.pathname === "/api/get-secure-links") {
      const key = url.searchParams.get("key");
      if (key === AUTH_KEY) {
        const tree = await getTreeWithCache();
        const secureData = {};
        tree.forEach((file, index) => {
          const pathSegments = file.path.split('/');
          pathSegments.pop(); // 去掉文件名
          const pathInfo = pathSegments.slice(-2).join('/') || 'root';
          // 将明文URL和真实路径打包返回
          secureData[index] = { url: `${BASE_URL}/${file.path}`, pathInfo: pathInfo };
        });
        return new Response(JSON.stringify({ success: true, data: secureData }), { headers: { "Content-Type": "application/json" } });
      }
      return new Response(JSON.stringify({ success: false, msg: "Auth Failed" }), { status: 403, headers: { "Content-Type": "application/json" } });
    }

    // ==========================================
    // 🔹 路由 4：根路径加载 HTML（脱敏状态，绝对无明文路径）
    // ==========================================
    if (url.pathname === "/" || url.pathname === "/index.html") {
      const tree = await getTreeWithCache();
      const publicMetadata = tree.map((file, index) => {
        const filename = file.path.split('/').pop();
        const ext = filename.split('.').pop().toLowerCase();
        return { id: index, filename, ext }; 
      });
      const rawHtmlString = typeof htmlTemplate === 'string' ? htmlTemplate : htmlTemplate.default;
      const finalHtml = rawHtmlString.replace('/*SERVER_DATA*/ []', JSON.stringify(publicMetadata));
      return new Response(finalHtml, { headers: { "Content-Type": "text/html; charset=utf-8" } });
    }

    // ==========================================
    // 🛡️ 路由 5：反向代理透传（边缘缓存护航）
    // ==========================================
    const cacheKeyRequest = new Request(request.url, { method: "GET", headers: request.headers });
    let cachedAsset = await edgeCache.match(cacheKeyRequest);
    if (cachedAsset) return cachedAsset; 

    const githubRawUrl = `https://raw.githubusercontent.com/${GITHUB_OWNER}/${GITHUB_REPO}/${GITHUB_BRANCH}${url.pathname}`;
    const proxyHeaders = new Headers(request.headers);
    if (GH_TOKEN) proxyHeaders.set("Authorization", `token ${GH_TOKEN}`);

    try {
      const gitHubResponse = await fetch(githubRawUrl, { method: request.method, headers: proxyHeaders, redirect: "follow" });
      if (gitHubResponse.ok) {
        const newResponse = new Response(gitHubResponse.body, gitHubResponse);
        newResponse.headers.set("Cache-Control", "public, max-age=7200");
        newResponse.headers.set("Access-Control-Allow-Origin", "*");
        if (request.method === "GET") await edgeCache.put(cacheKeyRequest, newResponse.clone());
        return newResponse;
      }
    } catch (err) {}

    return new Response("Asset Not Found In Config Repo", { status: 404 });
  }
};
