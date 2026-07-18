// 自动导入隔壁的纯 HTML 视图资产
import htmlTemplate from './index.html';

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    // ==========================================
    // 🔒 云端安全与大厂中转配置区
    // ==========================================
    const AUTH_KEY = env.SECRET_KEY || "614118"; 
    const GITHUB_OWNER = "zzgs219G"; 
    const GITHUB_REPO = "json"; 
    const GITHUB_BRANCH = "main"; 
    const GH_TOKEN = env.GH_TOKEN || ""; 
    const BASE_URL = "https://json.614118.xyz";

    // 使用 Cloudflare 强大的边缘原生 Cache API（需要绑定自定义域名生效）
    const edgeCache = caches.default;

    // ==========================================
    // 🌲 核心函数：带边缘缓存的 GitHub 目录树扫描
    // ==========================================
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
        const headers = { "User-Agent": "Cloudflare-Worker-Production-Router" };
        if (GH_TOKEN) { headers["Authorization"] = `token ${GH_TOKEN}`; }

        const ghResponse = await fetch(ghApiUrl, { headers });
        if (ghResponse.ok) {
          const treeData = await ghResponse.json();
          const filteredTree = treeData.tree.filter(node => 
            node.type === "blob" && 
            (node.path.endsWith(".json") || node.path.endsWith(".enc"))
          );

          // 将扫描结果在 CF 边缘机房强制缓存 12 小时，彻底干掉 GitHub Rate Limit 限制
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
    // ♻️ 路由 1：手动强制同步仓库（清空缓存）
    // ==========================================
    if (url.pathname === "/api/flush-cache") {
      const key = url.searchParams.get("key");
      if (key !== AUTH_KEY) return new Response("Unauthorized", { status: 401 });
      
      // 清空目录树缓存
      await getTreeWithCache(true);
      return new Response(JSON.stringify({ success: true, msg: "缓存清空，仓库同步成功" }), {
        headers: { "Content-Type": "application/json" }
      });
    }

    // ==========================================
    // ⚡ 路由 2：真·边缘高速网络按需测速
    // ==========================================
    if (url.pathname === "/api/ping") {
      const id = parseInt(url.searchParams.get("id"));
      const tree = await getTreeWithCache();
      const targetFile = tree[id];

      if (!targetFile) return new Response("Not Found", { status: 404 });

      try {
        const fullRealUrl = `${BASE_URL}/${targetFile.path}`;
        // 测速包去撞击本域名的反向代理路由（如果命中缓存则呈现 CDN 级别速度）
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
    // 🔹 路由 4：访问根路径下发高阶控制台面板
    // ==========================================
    if (url.pathname === "/" || url.pathname === "/index.html") {
      const tree = await getTreeWithCache();

      const publicMetadata = tree.map((file, index) => {
        const pathSegments = file.path.split('/');
        const filename = pathSegments.pop();
        const ext = filename.split('.').pop().toLowerCase();
        const pathInfo = pathSegments.slice(-2).join('/') || 'root';
        return { id: index, filename, ext, pathInfo };
      });

      const rawHtmlString = typeof htmlTemplate === 'string' ? htmlTemplate : htmlTemplate.default;
      if (!rawHtmlString) return new Response("HTML 加载失败", { status: 500 });

      const finalHtml = rawHtmlString.replace('/*SERVER_DATA*/ []', JSON.stringify(publicMetadata));
      return new Response(finalHtml, { headers: { "Content-Type": "text/html; charset=utf-8" } });
    }

    // ==========================================
    // 🛡️ 路由 5：【高级重构】带智能静态缓存的反向代理
    // 你的手机 APP 请求配置时，自动被全网节点拦截并缓存，响应速度和稳定性提升数十倍！
    // ==========================================
    const cacheKeyRequest = new Request(request.url, { method: "GET", headers: request.headers });
    let cachedAsset = await edgeCache.match(cacheKeyRequest);
    if (cachedAsset) return cachedAsset; // 🎯 命中边缘内存直接返回，0ms 延迟，不打扰 GitHub

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
        // 构建带浏览器/边缘强缓存头的新响应对象并写入节点缓存（默认缓存 2 小时）
        const newResponse = new Response(gitHubResponse.body, gitHubResponse);
        newResponse.headers.set("Cache-Control", "public, max-age=7200");
        newResponse.headers.set("Access-Control-Allow-Origin", "*");
        
        // 只有 GET 请求才允许被送入边缘物理缓存
        if (request.method === "GET") {
          await edgeCache.put(cacheKeyRequest, newResponse.clone());
        }
        return newResponse;
      }
    } catch (err) {}

    return new Response("Asset Not Found In Config Repo", { status: 404 });
  }
};
