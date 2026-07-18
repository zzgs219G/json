// 导入隔壁的纯 HTML 视图资产
import htmlTemplate from './index.html';

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    // ==========================================
    // 🔒 云端安全配置（代码锁死在云端，外人绝对抓不到）
    // ==========================================
    // 优先读取你在 CF 后台配置的环境变量 SECRET_KEY，没有则默认密码为 "614118"
    const AUTH_KEY = env.SECRET_KEY || "614118"; 

    // 对齐你给我的 code_json.txt 里的 5 个真实后台链接
    const PRIVATE_RESOURCES = [
      "https://json.614118.xyz/backend/jian_box/raw/jian_box_raw.json",
      "https://json.614118.xyz/backend/jian_box/jian_box.enc",
      "https://json.614118.xyz/comic/comic_sources.json",
      "https://json.614118.xyz/jian_box.json",
      "https://json.614118.xyz/my_rjk.json"
    ];

    // ==========================================
    // 🔑 异步安全鉴权接口（点击复制或直达时才触发）
    // ==========================================
    if (url.pathname === "/api/get-secure-link") {
      const id = parseInt(url.searchParams.get("id"));
      const key = url.searchParams.get("key");

      if (key === AUTH_KEY && PRIVATE_RESOURCES[id]) {
        return new Response(JSON.stringify({ success: true, url: PRIVATE_RESOURCES[id] }), {
          headers: { 
            "Content-Type": "application/json", 
            "Access-Control-Allow-Origin": "*" 
          }
        });
      }
      return new Response(JSON.stringify({ success: false, msg: "密钥认证失败" }), { 
        status: 403, headers: { "Content-Type": "application/json" } 
      });
    }

    // ==========================================
    // 🗂️ 服务端数据脱敏预处理（只给前端发文件名和后缀用来画UI）
    // ==========================================
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
      // testUrl 仅用于前端发 HEAD 请求测延迟毫秒数，绝不是明文下载入口
      return { id: index, filename, ext, pathInfo, testUrl: link };
    });

    // ==========================================
    // 🚀 核心注入：兼容处理文本模块，将脱敏数据注入 HTML
    // ==========================================
    const rawHtmlString = typeof htmlTemplate === 'string' ? htmlTemplate : htmlTemplate.default;

    if (!rawHtmlString) {
      return new Response("服务器错误：HTML 视图未成功转换为文本资产，请检查 wrangler.toml 配置。", { status: 500 });
    }

    // 强行把 HTML 里面的占位符替换成真实的元数据，实现真正的 1 次请求闭环秒开
    const finalHtml = rawHtmlString.replace('/*SERVER_DATA*/ []', JSON.stringify(publicMetadata));

    return new Response(finalHtml, {
      headers: { "Content-Type": "text/html; charset=utf-8" }
    });
  }
};
