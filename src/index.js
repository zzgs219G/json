// 自动导入隔壁的纯 HTML 视图资产
import htmlTemplate from './index.html';

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    // ==========================================
    // 🔒 云端安全配置（代码锁在云端，外人绝对抓不到）
    // ==========================================
    // 优先读取你在 CF 后台配置的环境变量 SECRET_KEY，没有则默认密码为 "614118"
    const AUTH_KEY = env.SECRET_KEY || "614118"; 

    // 🛠️ 已经焊死你的真实 GitHub 仓库信息
    const GITHUB_OWNER = "zzgs219G"; 
    const GITHUB_REPO = "json"; 
    const GITHUB_BRANCH = "main"; 
    
    // 💡 如果你的仓库是【私有仓库】，请务必在 CF 后台环境变量里配置 GH_TOKEN（你的 GitHub 个人访问令牌）
    const GH_TOKEN = env.GH_TOKEN || ""; 

    // 自动拼接的域名地址前缀
    const BASE_URL = "https://json.614118.xyz";

    // ==========================================
    // 🔑 异步安全鉴权接口（点击复制或直达时触发）
    // ==========================================
    if (url.pathname === "/api/get-secure-link") {
      const targetUrl = url.searchParams.get("url");
      const key = url.searchParams.get("key");

      if (key === AUTH_KEY && targetUrl && targetUrl.startsWith(BASE_URL)) {
        return new Response(JSON.stringify({ success: true, url: targetUrl }), {
          headers: { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*" }
        });
      }
      return new Response(JSON.stringify({ success: false, msg: "安全密钥认证失败" }), { 
        status: 403, headers: { "Content-Type": "application/json" } 
      });
    }

    // ==========================================
    // 🌲 核心黑魔法：动态去 GitHub API 抓取完整的项目结构目录树
    // ==========================================
    let publicMetadata = [];
    try {
      const ghApiUrl = `https://api.github.com/repos/${GITHUB_OWNER}/${GITHUB_REPO}/git/trees/${GITHUB_BRANCH}?recursive=1`;
      
      const headers = { "User-Agent": "Cloudflare-Worker-AutoIndex" };
      if (GH_TOKEN) { headers["Authorization"] = `token ${GH_TOKEN}`; }

      const ghResponse = await fetch(ghApiUrl, { headers });
      
      if (ghResponse.ok) {
        const treeData = await ghResponse.json();
        
        // 全自动过滤：只抓取真正的文件，且后缀必须是 .json 或者是 .enc
        const validFiles = treeData.tree.filter(node => 
          node.type === "blob" && 
          (node.path.endsWith(".json") || node.path.endsWith(".enc"))
        );

        // 动态拼装脱敏元数据发放给前端
        publicMetadata = validFiles.map((file, index) => {
          const pathSegments = file.path.split('/');
          const filename = pathSegments.pop();
          const ext = filename.split('.').pop().toLowerCase();
          const pathInfo = pathSegments.slice(-2).join('/') || 'root';
          const fullRealUrl = `${BASE_URL}/${file.path}`; // 全自动拼接真实网盘路径

          return { id: index, filename, ext, pathInfo, testUrl: fullRealUrl };
        });
      }
    } catch (e) {
      // 容错兜底
      publicMetadata = [{ id: 0, filename: "实时扫描仓库失败", ext: "error", pathInfo: "error", testUrl: "" }];
    }

    // ==========================================
    // 🚀 服务端合并与下发（真正的 1 次请求闭环秒开）
    // ==========================================
    const rawHtmlString = typeof htmlTemplate === 'string' ? htmlTemplate : htmlTemplate.default;
    if (!rawHtmlString) return new Response("服务器内部错误：HTML资产解析失败", { status: 500 });

    const finalHtml = rawHtmlString.replace('/*SERVER_DATA*/ []', JSON.stringify(publicMetadata));
    return new Response(finalHtml, { headers: { "Content-Type": "text/html; charset=utf-8" } });
  }
};
