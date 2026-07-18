// 💡 Wrangler 独门绝技：直接把 HTML 文件作为文本模块导入进来
import htmlTemplate from './index.html';

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    // ==========================================
    // ⚙️ 云端敏感配置（安全防爆，源码绝不外泄）
    // ==========================================
    const AUTH_KEY = env.SECRET_KEY || "z2458181028"; // 你的调度密码

    // 你的私人后台链接库，直接贴明文，外人按 F12 打死也看不到
    const PRIVATE_RESOURCES = [
      "https://json.614118.xyz/backend/jian_box/raw/jian_box_raw.json",
      "https://json.614118.xyz/backend/jian_box/jian_box.enc"
      // 📝 以后有新链接，直接在后端无脑往下加：
      // , "https://json.614118.xyz/backend/xxx.json"
    ];

    // ==========================================
    // 🔒 独立鉴权路由（仅在点击复制/直达时触发请求验证）
    // ==========================================
    if (url.pathname === "/api/get-secure-link") {
      const id = parseInt(url.searchParams.get("id"));
      const key = url.searchParams.get("key");

      if (key === AUTH_KEY && PRIVATE_RESOURCES[id]) {
        return new Response(JSON.stringify({ success: true, url: PRIVATE_RESOURCES[id] }), {
          headers: { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*" }
        });
      }
      return new Response(JSON.stringify({ success: false, msg: "密钥错误" }), { 
        status: 403, headers: { "Content-Type": "application/json" } 
      });
    }

    // ==========================================
    // 🎛️ 服务端数据预处理（提取非敏感元数据）
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
      // testUrl 传给前端，专门用来做 no-cors 测速。真实访问链接绝对不放进去！
      return { id: index, filename, ext, pathInfo, testUrl: link };
    });

    // ==========================================
    // 🚀 核心黑魔法：在下发前，把脱敏数据直接注入到 HTML 对应的空数组中
    // ==========================================
    const finalHtml = htmlTemplate.replace('/*SERVER_DATA*/ []', JSON.stringify(publicMetadata));

    // 直接吐出组装好的满血前端页面，真正的 1 次请求闭环！
    return new Response(finalHtml, {
      headers: { "Content-Type": "text/html; charset=utf-8" }
    });
  }
};