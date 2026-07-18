export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    // ==========================================
    // ⚙️ 核心安全配置区
    // ==========================================
    const AUTH_KEY = env.SECRET_KEY || "z2458181028"; // 你的操作密钥

    // 存放在云端内存的原始链接库，外人根本抓不到
    const PRIVATE_RESOURCES = [
      "https://json.614118.xyz/backend/jian_box/raw/jian_box_raw.json",
      "https://json.614118.xyz/backend/jian_box/jian_box.enc"
      // 📝 以后添加新链接，直接在后端无脑往下加：
      // , "https://json.614118.xyz/backend/xxx.json"
    ];

    // ==========================================
    // 🔌 路由 1：获取无害的脱敏公开资源列表
    // ==========================================
    if (url.pathname === "/api/list") {
      const publicMetadata = PRIVATE_RESOURCES.map((link, index) => {
        let filename = "未知资源";
        let ext = "default";
        let pathInfo = "root";
        try {
          const urlObj = new URL(link);
          const segments = urlObj.pathname.split('/');
          filename = segments.pop() || "未命名";
          ext = filename.split('.').pop().toLowerCase();
          pathInfo = segments.slice(-2).join('/') || 'root';
        } catch(e) {
          filename = link.substring(link.lastIndexOf('/') + 1);
        }
        return { id: index, filename, ext, pathInfo, testUrl: link };
      });
      return new Response(JSON.stringify(publicMetadata), {
        headers: { "Content-Type": "application/json" }
      });
    }

    // ==========================================
    // 🔒 路由 2：通过密钥高安全提取真实明文 URL
    // ==========================================
    if (url.pathname === "/api/get-secure-link") {
      const id = parseInt(url.searchParams.get("id"));
      const key = url.searchParams.get("key");

      if (key === AUTH_KEY && PRIVATE_RESOURCES[id]) {
        return new Response(JSON.stringify({ success: true, url: PRIVATE_RESOURCES[id] }), {
          headers: { "Content-Type": "application/json" }
        });
      }
      return new Response(JSON.stringify({ success: false, msg: "认证失败" }), { 
        status: 403, headers: { "Content-Type": "application/json" } 
      });
    }

    // ==========================================
    // 🗂️ 路由 3：兜底逻辑，如果是访问网页，自动渲染 index.html
    // ==========================================
    return env.ASSETS.fetch(request);
  }
};
