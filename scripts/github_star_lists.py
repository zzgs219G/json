#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GitHub 星标库 · 云端抓取脚本
================================
职责：从 GitHub 读取用户 zzgs219G 的所有公开 Star 列表（分类），
      生成分层 JSON 供 jian_box App 使用。

设计原则：
  - 只抓取公开列表（API 默认行为）
  - 分层存储：index / lists / repos
  - 只保留有 APK 的 Release
  - 原始下载链接不拼接镜像
  - 单仓库失败不影响整体
  - 所有网络请求带超时与重试（稳定性优先）

数据流：
  1. GraphQL 查询所有 Star 列表 → 获取每个分类的 ID 和名称
  2. 对每个分类，分页查询所有仓库
  3. 对每个仓库，调用 REST API 获取 Release 列表
  4. 生成三层 JSON 结构

环境变量：
  GH_STAR_TOKEN: GitHub Personal Access Token（需 user scope）
  GITHUB_STAR_USER: 目标用户名（可选，默认 zzgs219G）

用法：
  python scripts/github_star_lists.py --out backend/github_star
"""

import os
import json
import time
import argparse
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Any, Optional

import requests

# ============================================================================
# 配置
# ============================================================================

GITHUB_GRAPHQL_URL = "https://api.github.com/graphql"
GITHUB_REST_API = "https://api.github.com"

TARGET_USER = os.environ.get("GITHUB_STAR_USER", "zzgs219G")
MAX_LISTS = 50              # 最多获取 50 个分类
MAX_REPOS_PER_LIST = 100    # 每个分类分页获取，单页上限 100（GraphQL 连接上限）
MAX_RELEASES = 30           # 每个仓库最多获取 30 个 Release
REQUEST_DELAY = 0.5         # 请求间隔（秒），避免触发限流
REQUEST_TIMEOUT = 30        # 单次请求超时（秒）
MAX_RETRIES = 3             # 网络错误重试次数

CST = timezone(timedelta(hours=8))


# ============================================================================
# GraphQL 查询模板
# ============================================================================

QUERY_LISTS = """
query($login: String!, $first: Int!) {
  user(login: $login) {
    lists(first: $first) {
      nodes {
        id
        name
      }
    }
  }
}
"""

QUERY_LIST_ITEMS = """
query($listId: ID!, $first: Int!, $after: String) {
  node(id: $listId) {
    ... on UserList {
      name
      items(first: $first, after: $after) {
        pageInfo {
          hasNextPage
          endCursor
        }
        nodes {
          ... on Repository {
            nameWithOwner
            url
            description
            stargazerCount
            forkCount
            primaryLanguage {
              name
              color
            }
            pushedAt
            updatedAt
            createdAt
          }
        }
      }
    }
  }
}
"""


# ============================================================================
# GitHub API 客户端
# ============================================================================

class GitHubClient:
    def __init__(self, token: str):
        self.token = token
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "GitHub-Star-Lists-Crawler/1.0"
        })

    def _request_with_retry(self, method: str, url: str, **kwargs) -> requests.Response:
        """带超时与重试的请求，稳定性优先：网络抖动自动重试，最终失败抛异常给调用方处理"""
        last_exc = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = self.session.request(method, url, timeout=REQUEST_TIMEOUT, **kwargs)
                resp.raise_for_status()
                return resp
            except requests.exceptions.RequestException as e:
                last_exc = e
                if attempt < MAX_RETRIES:
                    wait = 2 ** attempt  # 1s, 2s, 4s
                    print(f"   ⚠️ 请求失败（第 {attempt} 次）：{e}，{wait}s 后重试")
                    time.sleep(wait)
        raise last_exc

    def graphql(self, query: str, variables: Dict[str, Any]) -> Dict[str, Any]:
        """执行 GraphQL 查询"""
        payload = {"query": query, "variables": variables}
        resp = self._request_with_retry("POST", GITHUB_GRAPHQL_URL, json=payload)
        return resp.json()

    def rest_get(self, endpoint: str) -> Dict[str, Any]:
        """执行 REST GET 请求"""
        url = f"{GITHUB_REST_API}{endpoint}"
        resp = self._request_with_retry("GET", url)
        return resp.json()


# ============================================================================
# 核心逻辑
# ============================================================================

def fetch_lists(client: GitHubClient) -> List[Dict[str, str]]:
    """
    获取用户的所有公开 Star 列表（分类）。
    注意：私有列表在 API 层面就被过滤，不会出现在返回结果中。
    """
    print(f"📁 获取用户 {TARGET_USER} 的 Star 列表...")
    result = client.graphql(QUERY_LISTS, {
        "login": TARGET_USER,
        "first": MAX_LISTS
    })

    # 检查是否有错误（比如 Token 权限不足）
    if "errors" in result:
        errors = result["errors"]
        print(f"   ⚠️ GraphQL 返回错误: {errors}")
        # 如果是权限问题，错误信息会包含 "Your token has not been granted the required scopes"
        for err in errors:
            if "scopes" in str(err) or "user" in str(err).lower():
                print("   ❌ Token 缺少 user scope，请重新生成带 user 权限的 Token")
                return []
        return []

    nodes = result.get("data", {}).get("user", {}).get("lists", {}).get("nodes", [])
    print(f"   找到 {len(nodes)} 个公开分类")

    return [{"id": node["id"], "name": node["name"]} for node in nodes]


def fetch_list_repos(client: GitHubClient, list_id: str) -> List[Dict[str, Any]]:
    """分页获取某个列表下的所有仓库"""
    repos = []
    after = None
    page = 0

    while True:
        page += 1
        result = client.graphql(QUERY_LIST_ITEMS, {
            "listId": list_id,
            "first": MAX_REPOS_PER_LIST,
            "after": after
        })

        node = result.get("data", {}).get("node")
        if not node:
            break

        items = node.get("items", {})
        nodes = items.get("nodes", [])

        if not nodes:
            break

        print(f"   分页 {page}: 获取 {len(nodes)} 个仓库")
        for repo in nodes:
            lang = repo.get("primaryLanguage")
            repos.append({
                "nameWithOwner": repo["nameWithOwner"],
                "repoUrl": repo["url"],
                "description": repo.get("description"),
                "stargazerCount": repo.get("stargazerCount", 0),
                "forkCount": repo.get("forkCount", 0),
                "primaryLanguage": {
                    "name": lang.get("name") if lang else None,
                    "color": lang.get("color") if lang else None
                } if lang else None,
                "pushedAt": repo.get("pushedAt"),
                "updatedAt": repo.get("updatedAt"),
                "createdAt": repo.get("createdAt")
            })

        page_info = items.get("pageInfo", {})
        if not page_info.get("hasNextPage"):
            break
        after = page_info.get("endCursor")

    return repos


def fetch_releases(client: GitHubClient, repo_full_name: str) -> List[Dict[str, Any]]:
    """
    获取某个仓库的 Releases，只保留有 APK 的 Release。
    如果获取失败，返回空列表。
    """
    try:
        data = client.rest_get(f"/repos/{repo_full_name}/releases?per_page={MAX_RELEASES}")
        time.sleep(REQUEST_DELAY)  # REST API 限流更严格
    except requests.exceptions.RequestException as e:
        print(f"   ⚠️ {repo_full_name} Releases 请求失败: {e}")
        return []
    except Exception as e:
        print(f"   ⚠️ {repo_full_name} Releases 解析失败: {e}")
        return []

    releases = []
    for rel in data:
        apks = []
        for asset in rel.get("assets", []):
            if asset.get("content_type") == "application/vnd.android.package-archive":
                apks.append({
                    "name": asset["name"],
                    "originalDownloadUrl": asset["browser_download_url"],
                    "size": asset.get("size", 0)
                })

        if apks:
            releases.append({
                "tagName": rel.get("tag_name", ""),
                "publishedAt": rel.get("published_at"),
                "apkAssets": apks,
                "sourceZipUrl": rel.get("zipball_url")
            })

    return releases


def sanitize_filename(name: str) -> str:
    """将分类名/仓库名转为安全的文件名"""
    # 替换不安全字符
    invalid_chars = r'<>:"/\\|?*'
    for ch in invalid_chars:
        name = name.replace(ch, '_')
    # 去除首尾空格
    name = name.strip()
    # 限制长度
    if len(name) > 50:
        name = name[:50]
    return name


# ============================================================================
# 主流程
# ============================================================================

def main():
    parser = argparse.ArgumentParser(description="GitHub 星标库抓取脚本")
    parser.add_argument("--out", default="backend/github_star",
                        help="输出目录（默认 backend/github_star）")
    args = parser.parse_args()

    token = os.environ.get("GH_STAR_TOKEN")
    if not token:
        print("❌ 环境变量 GH_STAR_TOKEN 未设置")
        print("   请在 GitHub Actions Secrets 中配置 GH_STAR_TOKEN")
        exit(1)

    client = GitHubClient(token)
    out_dir = Path(args.out)

    print(f"🚀 开始抓取用户 {TARGET_USER} 的星标列表...")
    start_time = time.time()

    # 1. 获取所有分类
    lists = fetch_lists(client)
    if not lists:
        print("⚠️ 未找到任何公开 Star 列表")
        print("   可能原因：")
        print("   1. Token 没有 user scope 权限")
        print("   2. 用户没有公开的 Star 列表")
        print("   3. GraphQL API 返回错误（检查上面的日志）")
        exit(0)

    print(f"\n📂 共 {len(lists)} 个分类: {[l['name'] for l in lists]}")

    # 2. 创建输出目录
    lists_dir = out_dir / "lists"
    repos_dir = out_dir / "repos"
    lists_dir.mkdir(parents=True, exist_ok=True)
    repos_dir.mkdir(parents=True, exist_ok=True)

    # 3. 遍历每个分类
    index_entries = []
    all_repos = {}  # 用 full_name 去重，同一个仓库可能出现在多个分类

    for idx, lst in enumerate(lists):
        list_name = lst["name"]
        list_id = lst["id"]

        print(f"\n📂 [{idx+1}/{len(lists)}] 分类: {list_name}")

        # 获取该分类下的所有仓库
        repos = fetch_list_repos(client, list_id)
        print(f"   仓库数: {len(repos)}")

        if not repos:
            # 空分类也记录，但写入空列表
            list_data = {
                "listName": list_name,
                "listId": list_id,
                "updatedAt": datetime.now(CST).isoformat(),
                "repos": []
            }
            list_file = lists_dir / f"{sanitize_filename(list_name)}.json"
            with open(list_file, "w", encoding="utf-8") as f:
                json.dump(list_data, f, ensure_ascii=False, indent=2)

            index_entries.append({
                "listId": list_id,
                "name": list_name,
                "repoCount": 0,
                "jsonPath": f"lists/{sanitize_filename(list_name)}.json"
            })
            continue

        # 处理每个仓库
        list_repos = []
        for repo in repos:
            full_name = repo["nameWithOwner"]
            safe_name = sanitize_filename(full_name.replace("/", "_"))

            # 去重：同一个仓库只获取一次 Release，所有分类共享同一份 releaseJsonPath
            if full_name not in all_repos:
                print(f"   🔍 获取 {full_name} 的 Releases...")
                releases = fetch_releases(client, full_name)

                if releases:
                    release_data = {
                        "nameWithOwner": full_name,
                        "releases": releases
                    }
                    release_path = repos_dir / f"{safe_name}.json"
                    with open(release_path, "w", encoding="utf-8") as f:
                        json.dump(release_data, f, ensure_ascii=False, indent=2)
                    all_repos[full_name] = f"repos/{safe_name}.json"
                else:
                    # 没有 APK Release，不生成文件
                    all_repos[full_name] = None

            # 每个仓库都带 releaseJsonPath 引用（跨分类去重时复用缓存）
            repo["releaseJsonPath"] = all_repos[full_name]
            list_repos.append(repo)

            time.sleep(REQUEST_DELAY)

        # 写入该分类的 JSON 文件
        list_data = {
            "listName": list_name,
            "listId": list_id,
            "updatedAt": datetime.now(CST).isoformat(),
            "repos": list_repos
        }
        list_file = lists_dir / f"{sanitize_filename(list_name)}.json"
        with open(list_file, "w", encoding="utf-8") as f:
            json.dump(list_data, f, ensure_ascii=False, indent=2)

        index_entries.append({
            "listId": list_id,
            "name": list_name,
            "repoCount": len(list_repos),
            "jsonPath": f"lists/{sanitize_filename(list_name)}.json"
        })

    # 4. 生成索引文件
    index_data = {
        "generatedAt": datetime.now(CST).isoformat(),
        "totalLists": len(index_entries),
        "lists": index_entries
    }
    index_path = out_dir / "index.json"
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(index_data, f, ensure_ascii=False, indent=2)

    elapsed = time.time() - start_time
    print(f"\n✅ 完成！耗时 {elapsed:.1f}s")
    print(f"   分类: {len(index_entries)} 个")
    print(f"   仓库: {len(all_repos)} 个（去重后）")
    print(f"   输出目录: {out_dir.absolute()}")
    print(f"\n📄 索引文件: {index_path}")
    print(f"   分类列表: {len(list(lists_dir.glob('*.json')))} 个文件")
    print(f"   仓库 Release: {len(list(repos_dir.glob('*.json')))} 个文件")


if __name__ == "__main__":
    main()
