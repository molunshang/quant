#!/usr/bin/env python3
"""
掘金量化 Python SDK 文档下载脚本
从 https://www.myquant.cn/docs2/sdk/python/ 下载完整文档并保存为 Markdown 格式

用法:
    python3 download_sdk_docs.py

输出:
    ./myquant_sdk_docs/          # 文档输出目录
"""

import os
import re
import time
import random
import urllib.request
import urllib.parse
import urllib.error
import html as html_mod

import html2text  # pip install html2text


# ============================================================
# 配置
# ============================================================

BASE_URL = "https://www.myquant.cn/docs2"
SDK_PATH = "/sdk/python"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
OUTPUT_DIR = "./myquant_sdk_docs"

# 请求间隔: 3-8 秒随机延迟 (反爬)
MIN_DELAY = 3
MAX_DELAY = 8

# 失败重试
MAX_RETRIES = 3

# ============================================================
# 页面清单 (从网站 JS 中提取的完整页面路径)
# ============================================================

ROOT_PAGES = [
    "快速开始",
    "策略程序架构",
    "变量约定",
    "数据结构",
    "枚举常量",
    "错误码",
]

API_PAGES = [
    "基本函数",
    "交易函数",
    "交易查询函数",
    "两融交易函数",
    "债券交易函数",
    "基金交易函数",
    "新股新债交易函数",
    "算法交易函数",
    "交易事件",
    "数据事件",
    "其他事件",
    "数据订阅",
    "动态参数",
    "标的池",
    "行情数据查询函数（免费）",
    "股票财务数据及基础数据函数（免费）",
    "股票增值数据函数（付费）",
    "期货基础数据函数（免费）",
    "期货增值数据函数（付费）",
    "基金增值数据函数（付费）",
    "可转债增值数据函数（付费）",
    "通用数据函数（免费）",
    "老版本数据函数",
    "其他函数",
]


# ============================================================
# 工具函数
# ============================================================

def log(msg):
    t = time.strftime("%H:%M:%S")
    print(f"[{t}] {msg}")


def url_encode_path(path):
    parts = path.split("/")
    return "/".join(urllib.parse.quote(p, safe="") for p in parts)


def build_url(page_name, subdir=None):
    if subdir:
        path = f"{SDK_PATH}/{subdir}/{page_name}.html"
    else:
        path = f"{SDK_PATH}/{page_name}.html"
    return f"{BASE_URL}{url_encode_path(path)}"


def fetch_page(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": f"{BASE_URL}{SDK_PATH}/",
    })
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read()
                charset = resp.headers.get_content_charset() or "utf-8"
                return data.decode(charset, errors="replace")
        except Exception as e:
            log(f"  请求失败 (尝试 {attempt}/{MAX_RETRIES}): {e}")
            if attempt < MAX_RETRIES:
                wait = random.uniform(3, 6)
                log(f"  等待 {wait:.1f}s 后重试...")
                time.sleep(wait)
            else:
                raise


# ============================================================
# HTML → Markdown 转换 (使用 html2text)
# ============================================================

def rewrite_link(href, base_url=BASE_URL, sdk_path=SDK_PATH):
    """将内部链接 .html 重写为 .md 路径，外部链接保持原样"""
    if not href:
        return href
    # 提取相对路径
    if base_url in href:
        href = href.split(base_url, 1)[1]
    # 分离锚点
    anchor = ""
    if "#" in href:
        href, anchor = href.split("#", 1)
    # 只处理 SDK 下的 .html 链接
    if sdk_path in href and href.endswith(".html"):
        rel = href.split(sdk_path + "/", 1)[1].replace(".html", "")
        return f"{rel}.md#{anchor}" if anchor else f"{rel}.md"
    # 非 SDK 链接原样返回（保留锚点）
    return f"{href}#{anchor}" if anchor else href


def extract_content(html):
    """从 VuePress SSR HTML 中提取 theme-default-content 区域的 HTML"""
    marker = 'class="theme-default-content content__default">'
    start = html.find(marker)
    if start == -1:
        return None
    start += len(marker)
    # 用简单计数找到匹配的 </div>
    depth = 1
    i = start
    while depth > 0 and i < len(html):
        op = html.find("<div", i)
        cl = html.find("</div>", i)
        if cl == -1:
            return None
        if op != -1 and op < cl:
            depth += 1
            i = op + 4
        else:
            depth -= 1
            i = cl + 6
    return html[start:i - 6] if depth == 0 else None


def convert_content_to_markdown(content_html, page_name):
    """
    将 content HTML 转为 Markdown。
    先用 html2text 处理标准元素，再修正链接路径。
    """
    # 1) 预处理：移除 header-anchor 的 # 符号
    content_html = re.sub(
        r'<a[^>]*class="header-anchor"[^>]*>#</a>', "", content_html
    )

    # 2) html2text 转换
    converter = html2text.HTML2Text()
    converter.body_width = 0           # 不自动换行
    converter.protect_links = True     # 保护链接不被截断
    converter.skip_internal_links = False
    converter.decode_errors = "replace"
    converter.unicode_snob = True      # 用 Unicode 字符而非 ASCII
    converter.mark_code = True         # 用 ``` 标记代码块
    converter.escape_snob = True       # 保留转义字符

    markdown = converter.handle(content_html)

    # 3) 后处理
    # 3a) 修正内部链接路径
    def fix_link(m):
        prefix = m.group(1)
        text = m.group(2)
        url = m.group(3)
        new_url = rewrite_link(url.split(">")[0])  # 去掉表格管道符遗留的 >
        return f"{prefix}[{text}]({new_url})"

    markdown = re.sub(r'(!?\[)([^\]]*?)\]\(([^)]*?)\)', fix_link, markdown)

    # 3b) 清理链接末尾泄漏的 None (html2text 残留)
    markdown = re.sub(r'(\([^)]*?\))\)None', r'\1)', markdown)

    # 3c) [code] / [/code] → ``` (并保留语言标注)
    def fix_code_block(m):
        lang = ""
        prev = m.string[: m.start()].strip().split("\n")[-1] if m.start() else ""
        return f"```{lang}"

    markdown = markdown.replace("[code]", "```")
    markdown = markdown.replace("[/code]", "```")

    # 3d) 链接末尾的垃圾字符清理
    markdown = re.sub(r'\]\([^)]*?\)\s*None', '', markdown)

    # 3e) 修复 `<code>` 内链的双括号: [[text](url) → [text](url)
    markdown = re.sub(r'\[\[([^\]]*)\]\((https?://[^)]*|[^)]*\.md[^)]*)\)', r'[\1](\2)', markdown)

    # 3f) 移除多余空行，保持整洁
    markdown = re.sub(r'\n{3,}', '\n\n', markdown)

    return markdown.strip() + "\n"


# ============================================================
# 主流程
# ============================================================

def download_page(page_name, subdir=None):
    """下载单页并转换为 Markdown，返回 (title, markdown) 或 None"""
    url = build_url(page_name, subdir)
    log(f"下载中: {url}")
    try:
        html = fetch_page(url)
    except Exception as e:
        log(f"  ❌ 下载失败: {e}")
        return None

    # 提取标题
    m = re.search(r"<title>(.*?)</title>", html)
    title = m.group(1).replace(" - 掘金量化", "").strip() if m else page_name

    # 提取内容区域
    content_html = extract_content(html)
    if not content_html:
        log(f"  ⚠️ 无法找到内容区域")
        return None

    # 转 Markdown
    markdown = convert_content_to_markdown(content_html, page_name)
    log(f"  ✅ {len(markdown)} 字符")
    return title, markdown


def save_page(page_name, markdown, subdir=None):
    if subdir:
        out_dir = os.path.join(OUTPUT_DIR, subdir)
    else:
        out_dir = OUTPUT_DIR
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{page_name}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(markdown)
    log(f"  💾 {path}")


def main():
    log("=" * 60)
    log("掘金量化 Python SDK 文档下载器")
    log("=" * 60)
    log(f"输出目录: {os.path.abspath(OUTPUT_DIR)}")
    log(f"请求延迟: {MIN_DELAY}-{MAX_DELAY} 秒")
    log("")

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    total = len(ROOT_PAGES) + len(API_PAGES)
    ok = 0
    fail = 0

    all_pages = []

    # ---- 根目录页面 ----
    log(f"\n📄 根目录 ({len(ROOT_PAGES)} 页)")
    log("-" * 40)

    for idx, name in enumerate(ROOT_PAGES, 1):
        log(f"\n[{idx}/{total}] {name}")
        try:
            result = download_page(name)
            if result:
                title, md = result
                save_page(name, md)
                ok += 1
                all_pages.append((name, False))
            else:
                fail += 1
        except Exception as e:
            log(f"  ❌ 错误: {e}")
            fail += 1

        if idx < len(ROOT_PAGES):
            delay = random.uniform(MIN_DELAY, MAX_DELAY)
            log(f"  等待 {delay:.1f}s...")
            time.sleep(delay)

    # ---- API 介绍页面 ----
    log(f"\n📄 API 介绍 ({len(API_PAGES)} 页)")
    log("-" * 40)

    for idx, name in enumerate(API_PAGES, 1):
        global_idx = len(ROOT_PAGES) + idx
        log(f"\n[{global_idx}/{total}] API介绍/{name}")
        try:
            result = download_page(name, subdir="API介绍")
            if result:
                title, md = result
                save_page(name, md, subdir="API介绍")
                ok += 1
                all_pages.append((name, True))
            else:
                fail += 1
        except Exception as e:
            log(f"  ❌ 错误: {e}")
            fail += 1

        if idx < len(API_PAGES):
            delay = random.uniform(MIN_DELAY, MAX_DELAY)
            log(f"  等待 {delay:.1f}s...")
            time.sleep(delay)

    # ---- README 索引 ----
    lines = ["# 掘金量化 Python SDK 文档\n"]
    lines.append(f"> 共 {total} 页，成功 {ok}，失败 {fail}\n")
    lines.append("## 根目录\n")
    for name, _ in all_pages:
        if not _:
            lines.append(f"- [{name}]({name}.md)\n")
    lines.append("\n## API 介绍\n")
    for name, is_api in all_pages:
        if is_api:
            lines.append(f"- [{name}](API介绍/{name}.md)\n")
    with open(os.path.join(OUTPUT_DIR, "README.md"), "w", encoding="utf-8") as f:
        f.writelines(lines)
    log(f"  💾 README.md")

    log("")
    log("=" * 60)
    log(f"   完成!  成功 {ok}/{total}")
    log(f"   输出: {os.path.abspath(OUTPUT_DIR)}")
    log("=" * 60)


if __name__ == "__main__":
    main()
