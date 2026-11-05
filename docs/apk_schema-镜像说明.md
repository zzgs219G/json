# apk_schema 镜像说明（含 2026-11 仓库迁移）

> 本文只讲一件事：`backend/apk_schema/` 里的 JSON 从哪来、怎么更新、
> 以及为什么 2026-11 之后源仓库名变了。
> **与代码冲突时以代码为准**（`scripts/apk_schema_sync.py`）。

---

## 0. 三十秒定位

```
backend/apk_schema/v1/config-schema.json
  ↑ 镜像自：zzgs219G/artifact-pipeline 的 configs/v1/config-schema.json
  ↑ 抓取脚本：scripts/apk_schema_sync.py
  ↑ 自动触发：.github/workflows/apk-schema.yml
```

用途：App 端 `ProtectSchemaLoader` 读取这份 schema 来渲染加固配置界面。
镜像到本仓库是为了**不让 App 的请求 URL 暴露源仓库坐标**
（原先直连 `raw.giteeusercontent.com/zzgs219/...`，抓包即见）。

---

## 1. ⚠️ 2026-11 仓库迁移（最重要的一条）

| 项 | 迁移前 | 迁移后 |
|---|---|---|
| 源仓库 | `zzgs219G/apk-protect-action` | **`zzgs219G/artifact-pipeline`** |
| 可见性 | 公开 | 公开（`artifact-pipeline`）／**私有**（旧 `apk-protect-action`） |

**旧仓库 `apk-protect-action` 仍然存在，但已转为私有。** 所以：

- 脚本里若 `SOURCE_REPO` 忘了改 → 匿名拉取直接 404 → workflow 变红
- **不会**静默用旧数据（`fetch()` 抛 `RuntimeError` → `exit 1`），这是刻意保留的 fail-fast
- 任何新写的抓取逻辑都必须用 `artifact-pipeline`，**不要照抄历史文档/注释里的旧名**

发现时机：2026-11-05 —— 镜像文件停在 2026-10-20（3057 字节），
而源已到 2026-11-02（3617 字节），**落后半个月无人察觉**。
根因就是这里说的「脚本没跟着改名」，而 fail-fast 没生效是因为
旧仓库当时还是公开的（匿名能拉，所以一直没报错）。

---

## 2. 两个 secret（缺一个就断一环）

| Secret | 配在哪个仓库 | 权限 | 作用 |
|---|---|---|---|
| `JSON_REPO_DISPATCH_TOKEN` | `artifact-pipeline` | 对 `json` 仓库的 **Contents: Read and write** | 源仓库 push `configs/**` 时向本仓库发 `repository_dispatch` |
| `GH_SCHEMA_SOURCE_TOKEN` | **`json`（本仓库）** | 对 `artifact-pipeline` 的 **Contents: Read** | 本仓库拉取时带认证，绕过匿名限流（60 次/小时/IP） |

**注意它们是两个不同的 token，配在两个不同的仓库。**
2026-11-05 曾出现过「只删了其中一个」导致自动同步静默失效的情况。

`GH_SCHEMA_SOURCE_TOKEN` 的技术上可选（源是公开仓库，匿名也能拉），
但匿名会遇到 API 限流（HTTP 403 `rate limit exceeded`），
而脚本对 403 的处理是 `exit 1` → workflow 变红。**建议保留。**

---

## 3. 完整链路

```
artifact-pipeline 改 configs/v1/config-schema.json
      ↓ push
artifact-pipeline/.github/workflows/notify-json-mirror.yml   ← 在【源仓库】，不在本仓库
      ↓ 用 JSON_REPO_DISPATCH_TOKEN 发 repository_dispatch
      ↓ 事件类型：apk-schema-updated
      ↓ 期望响应：HTTP 204
json/.github/workflows/apk-schema.yml（repository_dispatch 触发）
      ↓
scripts/apk_schema_sync.py
      ↓ 用 GH_SCHEMA_SOURCE_TOKEN 走 GitHub Contents API
      ↓ Accept: application/vnd.github.raw
backend/apk_schema/v{N}/config-schema.json
      ↓ 有变更才提交（commit message 带 [skip ci]）
```

2026-11-05 实测通过：`HTTP 204` → json 侧 run 12s success。

---

## 4. 手动操作

```bash
# 只检查不写（需要 token，否则匿名限流会 403）
GH_SCHEMA_SOURCE_TOKEN=$(gh auth token) python3 scripts/apk_schema_sync.py --dry-run

# 实际同步
GH_SCHEMA_SOURCE_TOKEN=$(gh auth token) python3 scripts/apk_schema_sync.py

# 只同步某个版本
python3 scripts/apk_schema_sync.py --version 1
```

**新增 schema 大版本**：改 `SUPPORTED_VERSIONS` 前，先确认 App 端
`ProtectConfigSchema.MAX_SUPPORTED_SCHEMA_VERSION` 已支持该版本 ——
否则 App 会读到它渲染不了的 schema。

---

## 5. 已知小问题（不影响功能，未修）

`write_if_changed()` 打印的是 `len(content)`（**字符数**），不是字节数。
含中文的 JSON 会出现「报告 2925 字节 / 实际 3617 字节」的偏差。
只影响日志可读性，文件内容正确（已用 `diff` 与源仓库逐字节验证）。

---

## 6. 排查清单

| 现象 | 先查这个 |
|---|---|
| 镜像内容停在某天不动 | ① `SOURCE_REPO` 是否还是旧名 ② 源仓库是否转为私有 ③ `JSON_REPO_DISPATCH_TOKEN` 是否还在 |
| workflow 报 HTTP 403 | 匿名限流。配 `GH_SCHEMA_SOURCE_TOKEN`，或临时用 `gh auth token` |
| workflow 报 HTTP 404 | 源仓库名错，或源已私有 |
| 源仓库 push 了但本仓库没动静 | 去 **artifact-pipeline**（不是本仓库）看 `notify-json-mirror.yml` 的日志，找 `跳过镜像同步`（说明 secret 缺失） |
| App 显示配置界面异常 | `backend/apk_schema/v{N}/` 的版本与 App `MAX_SUPPORTED_SCHEMA_VERSION` 是否匹配 |
