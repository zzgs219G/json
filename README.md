# json

数据镜像仓库。存放 App 运行时读取的各类数据文件。

## 目录

| 路径 | 内容 | 更新方式 | 说明 |
|---|---|---|---|
| `backend/apk_schema/` | 加固配置 schema | 源仓库 push 时自动同步 | **[镜像说明](docs/apk_schema-镜像说明.md)** |
| `backend/antutu/` | 安兔兔排行 | 定时抓取 | |
| `backend/github_star/` | GitHub Star 榜单 | 定时抓取 | |
| `backend/github_trending/` | GitHub Trending | 定时抓取 | |
| `backend/jian_box/` | 简盒数据 | | |
| `backend/jian_zhu_shou/` | 简助手数据 | | |
| `backend/online_dialog/` | 在线弹窗配置 | | |
| `backend/demo/` | 示例 | | |

## 为什么有 `backend/` 前缀

App 直连 `raw.githubusercontent.com/...` 时，路径里的 owner/repo
会暴露在请求 URL 中（抓包或反编译即见）。镜像到本仓库后，
App 只需知道本仓库地址，不额外暴露数据来源。

## 约定

- 自动更新的数据文件，commit message 带 `[skip ci]`
- 抓取脚本在 `scripts/`，工作流在 `.github/workflows/`
- 迁移/排查类的背景说明放 `docs/`
