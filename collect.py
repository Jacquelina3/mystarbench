#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GitHub 每日涨星 Top10 —— 抓官方 Trending 页面、重排、写数据文件、推送

只用 Python 标准库，无需安装任何依赖。

用法:
    python collect.py              # 抓取、生成榜单、写数据文件、推送（如果已配置）
    python collect.py --no-push    # 只抓取和写数据文件，不发通知/邮件
    python collect.py --from-raw   # 不直连，改读 data/trending_daily.md / trending_weekly.md 原文

--from-raw 用在本机直连 github.com 超时的情况：由 WorkBuddy 的网页抓取通道把官方
Trending 页面原文写入上面两个 .md，本脚本再从原文里正则提取数字（不经过任何转述
或估算）。直连失败时也会自动回退到这两份原文，且要求原文是当天保存的。

数据源说明（这一点很关键，避免再出现「对不上」的问题）:
    直接读取 GitHub 官方 Trending 页面的原文：
      日榜 https://github.com/trending?since=daily     -> "N stars today"
      周榜 https://github.com/trending?since=weekly    -> "N stars this week"
    页面上的数字是 GitHub 自己统计的，本脚本只做「誊写 + 按数值降序重排」，
    不做任何估算。因此项目名和星数可以与网页逐条对照。

    Trending 页面自身的展示顺序不是按涨星数排的（官方有加权），
    所以本脚本会重新排序——这也是你要的「涨星最多的前十名」。

    页面只列约 25 个仓库，是 GitHub 筛过的候选池，不是全站全量扫描。

输出文件:
    data/latest.json   今日 Top10（以及全部候选 items_all）
    data/weekly.json   近 7 天 Top10（以及全部候选 items_all）
    data/history.json  逐日总星数快照，每天追加一天，供折线图使用
    data/daily/<date>.json  当天归档
"""
from __future__ import annotations

import json
import re
import sys
import gzip
import smtplib
import urllib.request
import urllib.parse
import urllib.error
import html as htmllib
from datetime import datetime, timezone, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import Header
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = Path(__file__).resolve().parent
DATA = BASE / "data"
DAILY = DATA / "daily"
LATEST = DATA / "latest.json"
WEEKLY = DATA / "weekly.json"
HISTORY = DATA / "history.json"
CONFIG = BASE / "config.json"
RAW_DAILY = DATA / "trending_daily.md"
RAW_WEEKLY = DATA / "trending_weekly.md"

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
TOP_N = 10
TRENDING = "https://github.com/trending"
HISTORY_DAYS = 7


# --------------------------------------------------------------------------
# 抓取
# --------------------------------------------------------------------------
def fetch(url: str, tries: int = 3, timeout: int = 30) -> str:
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": UA,
                    "Accept": "text/html,application/xhtml+xml",
                    "Accept-Language": "en-US,en;q=0.9",
                    "Accept-Encoding": "gzip",
                },
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                return raw.decode("utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001
            last = exc
            if i + 1 < tries:
                print(f"  ! 请求失败，重试 {i + 1}/{tries - 1}: {exc}", flush=True)
    raise RuntimeError(f"无法访问 {url}: {last}")


def parse_trending_md(text: str, unit_pat: str) -> list:
    """解析网页抓取通道返回的 Trending 页面 markdown 原文。

    兼容两种返回排版（同一通道不同时刻可能给不同形态）：
      A) 带链接：## [owner / name](https://github.com/owner/name)
         Python [103,945](.../stargazers) [12,154](.../forks) ... 2,373 stars today
      B) 纯文本：## owner / name
         Python 10,487 853 Built by ... 9,327 stars this week
    所有数字都用正则从原文里取，不做估算、不转述。
    """
    chunks = re.split(r"(?m)^##\s+", text)[1:]
    items = []
    for ch in chunks:
        lines = ch.splitlines()
        title = lines[0].strip() if lines else ""

        m = re.search(r"https://github\.com/([^/)\s]+)/([^/)\s]+)\)", title)
        if m:
            owner, name = m.group(1), m.group(2)
        else:
            bare = re.sub(r"^\[|\]$", "", title).strip()
            parts = [x.strip() for x in bare.split("/")]
            if len(parts) != 2 or not parts[0] or not parts[1]:
                continue
            owner, name = parts

        gain_line = next((l for l in lines if re.search(unit_pat, l)), "")
        if not gain_line:
            continue
        gm = re.search(r"([\d,]+)\s+" + unit_pat, gain_line)
        if not gm:
            continue
        gain = int(gm.group(1).replace(",", ""))

        # 数据行里可能夹着 "Built by" 头像链接，URL 中的数字会污染星数提取，先剥掉
        clean = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", gain_line)
        clean = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", clean)

        # 总星数：优先 stargazers 链接里的数字；否则取数据行里第一个数字
        stars = 0
        sm = re.search(r"\[([\d,]+)\]\([^)]*stargazers\)", gain_line)
        if sm:
            stars = int(sm.group(1).replace(",", ""))
        else:
            rest = re.sub(r"[\d,]+\s+" + unit_pat, "", clean)
            nums = re.findall(r"\d[\d,]*", rest)
            if nums:
                stars = int(nums[0].replace(",", ""))

        lang = ""
        lm = re.match(r"\s*([A-Za-z][A-Za-z0-9+#.\-]*)", clean)
        if lm:
            lang = lm.group(1)

        gi = lines.index(gain_line) if gain_line in lines else len(lines)
        desc_parts = []
        for l in lines[1:gi]:
            l = l.strip()
            if not l or l in ("Star", "Sponsor"):
                continue
            l = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", l)
            l = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", l)
            l = l.replace("**", "").strip()
            if l:
                desc_parts.append(l)
        desc = re.sub(r"\s+", " ", " ".join(desc_parts)).strip()

        if owner and name and stars > 0 and gain > 0:
            items.append({
                "repo": f"{owner}/{name}", "owner": owner, "name": name,
                "url": f"https://github.com/{owner}/{name}",
                "description": desc, "language": lang,
                "stars": stars, "gain": gain,
            })
    return items


def read_raw(kind: str, now: datetime) -> str:
    """读取网页抓取通道保存的官方页面原文；必须是当天写的，防止旧页面冒充今天。"""
    path = RAW_DAILY if kind == "daily" else RAW_WEEKLY
    if not path.exists():
        raise RuntimeError(f"缺少原文文件 {path.name}，无法使用 --from-raw 模式")
    written = datetime.fromtimestamp(path.stat().st_mtime).astimezone().date()
    if written != now.date():
        raise RuntimeError(
            f"{path.name} 是 {written} 保存的，不是今天；拒绝用旧页面冒充今日榜单"
        )
    text = path.read_text(encoding="utf-8")
    if len(text) < 200:
        raise RuntimeError(f"{path.name} 内容过短，可能没抓全")
    return text


def get_page(kind: str, unit_pat: str, now: datetime, from_raw: bool) -> tuple:
    """取页面并返回 (原文, 解析器)。直连失败时自动回退到当天保存的原文。"""
    if not from_raw:
        try:
            return fetch(f"{TRENDING}?since={kind}", tries=1, timeout=12), parse_trending
        except Exception as exc:  # noqa: BLE001
            print(f"  ! 直连 GitHub 失败（{exc}），尝试回退到当天保存的原文", flush=True)
            try:
                return read_raw(kind, now), parse_trending_md
            except Exception as e2:  # noqa: BLE001
                raise RuntimeError(
                    f"直连 GitHub {kind} 页失败（{exc}），且没有可用的当天原文（{e2}）。"
                    "云端请用 GitHub Actions 直连；本机请用 --from-raw 并先保存页面原文。"
                ) from e2
    return read_raw(kind, now), parse_trending_md


def strip_tags(s: str) -> str:
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", htmllib.unescape(s)).strip()


def parse_trending(page: str, unit_pat: str) -> list:
    """把 Trending 页面切成一条条仓库，逐条抽出仓库信息。

    云端（GitHub Actions）直连拿到的是原始 HTML；这里对 class 名、属性顺序、
    标签分隔都做了兜底，避免页面微调就解析出 0 条、或涨星数全是 0。
    """
    # 先按 Box-row 切；匹配不到就退回任意 <article>
    articles = re.split(r'<article\b[^>]*class="[^"]*\bBox-row\b[^"]*"[^>]*>', page)[1:]
    if not articles:
        articles = re.split(r"<article\b[^>]*>", page)[1:]
    items = []
    for block in articles:
        href = re.search(r'<h2[^>]*>\s*<a[^>]*href="/([^"/]+)/([^"/]+)"', block)
        if not href:
            href = re.search(r'href="/([^"/]+)/([^"/]+)"[^>]*>\s*<span', block)
        if not href:
            href = re.search(r'href="/([^"/]+)/([^"/]+)"', block)
        if not href:
            continue
        owner, name = href.group(1), href.group(2)

        # 先把标签剥成空格再找 "N stars today"：数字和文字之间可能夹着 svg/span
        plain = strip_tags(block)
        m = re.search(r"([\d,]+)\s+" + unit_pat, plain)
        gain = int(m.group(1).replace(",", "")) if m else 0

        stars = 0
        m = re.search(r'href="[^"]*/stargazers"[^>]*>(.*?)</a>', block, re.S)
        if m:
            inner = strip_tags(m.group(1))
            num = re.search(r"([\d,]+)", inner)
            if num:
                stars = int(num.group(1).replace(",", ""))

        lang = ""
        m = re.search(r'itemprop="programmingLanguage"[^>]*>([^<]+)<', block)
        if m:
            lang = htmllib.unescape(m.group(1)).strip()

        desc = ""
        m = re.search(r"<p\b[^>]*>(.*?)</p>", block, re.S)
        if m:
            desc = strip_tags(m.group(1))

        items.append(
            {
                "repo": f"{owner}/{name}",
                "owner": owner,
                "name": name,
                "url": f"https://github.com/{owner}/{name}",
                "description": desc,
                "language": lang,
                "stars": stars,
                "gain": gain,
            }
        )
    return items


def load_prev_snapshot(now: datetime, prev_weekly: dict | None = None) -> dict:
    """取「今天之前最近一天」的星数表，用于算日榜的次日差值，以及页面上的昨日星数。

    来源优先级：
      1. history.json 里今天之前最近一天的实测快照（最准）
      2. 上一次的 weekly.json：stars − stars this week = 7 天前星数，
         与今天相隔约 1 天，作为「昨日星数」的近似值
    两条都用不上就返回空，页面会如实显示「无昨日数据」，绝不编造。
    """
    prev = {}
    if prev_weekly:
        for it in prev_weekly.get("items_all") or prev_weekly.get("items") or []:
            base = int(it.get("stars") or 0) - int(it.get("gain") or 0)
            if base > 0:
                prev[it["repo"]] = base
    if HISTORY.exists():
        try:
            days = json.loads(HISTORY.read_text(encoding="utf-8")).get("days", {})
        except Exception:  # noqa: BLE001
            days = {}
        today = now.date().isoformat()
        past = [d for d in sorted(days) if d < today]
        if past:
            measured = days[past[-1]] or {}
            prev.update({k: v for k, v in measured.items() if v})
    return prev


def rank(items: list, top_n: int = TOP_N, prev: dict | None = None) -> list:
    """按涨星数降序，同名去重，补齐 rank / 增幅 / 昨日星数。items_all 保留完整候选池。"""
    prev = prev or {}
    seen, uniq = set(), []
    for it in items:
        if it["repo"] in seen:
            continue
        seen.add(it["repo"])
        uniq.append(it)
    uniq.sort(key=lambda x: (-x["gain"], -x["stars"]))

    for idx, it in enumerate(uniq, 1):
        it["rank"] = idx
        base = it["stars"] - it["gain"]
        it["growth_pct"] = round(it["gain"] / base * 100, 1) if base > 0 else None
        # 昨日总星数：能查到就给真实值，查不到就留 null，前端会标「无昨日数据」
        y = prev.get(it["repo"])
        if y and y > 0:
            it["stars_prev"] = int(y)
            it["gain_prev"] = int(it["stars"]) - int(y)
            it["growth_prev_pct"] = round(it["gain_prev"] / y * 100, 1)
        else:
            it["stars_prev"] = None
            it["gain_prev"] = None
            it["growth_prev_pct"] = None
    return uniq


def collect(now: datetime, from_raw: bool = False) -> dict:
    daily_text, daily_parser = get_page("daily", r"stars today", now, from_raw)
    daily_items = daily_parser(daily_text, r"stars today")
    how = "原文文件" if daily_parser is parse_trending_md else "直连页面"
    print(f"  · 日榜解析到 {len(daily_items)} 个仓库（{how}）", flush=True)
    if not daily_items:
        raise RuntimeError("日榜页面解析失败：0 个仓库，页面结构可能变了")
    if not any(it["gain"] > 0 for it in daily_items):
        raise RuntimeError("日榜解析到的 stars today 全是 0，页面结构可能变了，拒绝生成")

    weekly_text, weekly_parser = get_page("weekly", r"stars this week", now, from_raw)
    weekly_items = weekly_parser(weekly_text, r"stars this week")
    how = "原文文件" if weekly_parser is parse_trending_md else "直连页面"
    print(f"  · 周榜解析到 {len(weekly_items)} 个仓库（{how}）", flush=True)

    if not weekly_items:
        raise RuntimeError("周榜页面解析失败：0 个仓库，页面结构可能变了")
    if not any(it["gain"] > 0 for it in weekly_items):
        raise RuntimeError("周榜解析到的 stars this week 全是 0，页面结构可能变了，拒绝生成")

    prev = load_prev_snapshot(now, load_weekly())

    # 日榜只认官方页面标注的 stars today。候选不足 Top10 时如实少显示几条，绝不凑数。
    # 历史教训（两条路都已废弃，勿再改回去）：
    #   1. 用周榜 gain 补 → 把「7 天涨星」混进「今日涨星」；
    #   2. 用「昨日快照 → 今日」星数差值补 → 昨日快照本身可能不可靠，跨天后会把
    #      7 天量级的增量算成今日涨星（NVIDIA/auto-ontology 曾以 +9,368 错误登顶）。
    # 补出来的 gain 也不是页面标注值，口径上本就不该进按 stars today 排序的榜。
    if len(daily_items) < TOP_N:
        print(f"  · 日榜仅 {len(daily_items)} 个候选（GitHub 当天页面本身如此），少于 {TOP_N}；"
              f"如实少显示，不凑数、不用任何估算值补齐", flush=True)

    today = now.date().isoformat()
    stamp = now.isoformat()

    daily_ranked = rank(daily_items, prev=prev)
    weekly_ranked = rank(weekly_items, prev=prev)
    prev_days = []
    if HISTORY.exists():
        try:
            prev_days = sorted(json.loads(HISTORY.read_text(encoding="utf-8")).get("days", {}))
        except Exception:  # noqa: BLE001
            prev_days = []
    prev_days = [d for d in prev_days if d < today]

    latest = {
        "generated_at": stamp,
        "date": today,
        "window_hours": 24,
        "seed": False,
        "source": f"GitHub 官方 Trending 日榜 stars today · {today} 页面读取 · 按数值降序",
        "source_detail": "直接请求 https://github.com/trending?since=daily，逐条读取页面上的 stars today 原文数字后按数值降序重排；不做估算。Trending 页面原顺序含加权，与本榜顺序不同属正常。",
        "prev_date": prev_days[-1] if prev_days else "",
        "prev_note": (
            f"昨日星数取自 {prev_days[-1]} 的实测快照。"
            if prev_days else
            "还没有历史实测快照，昨日星数取自上一份周榜反推值（总星 − 本周新增），属近似值。"
        ),
        "candidate_count": len(daily_ranked),
        "items": daily_ranked[:TOP_N],
        "items_all": daily_ranked,
    }
    weekly = {
        "generated_at": stamp,
        "date": today,
        "window_days": 7,
        "seed": False,
        "source": f"GitHub 官方 Trending 周榜 stars this week · {today} 页面读取 · 按数值降序",
        "source_detail": "直接请求 https://github.com/trending?since=weekly，逐条读取 stars this week 原文数字后按数值降序重排；保存全部候选，供「按增幅」重新取前 10。",
        "candidate_count": len(weekly_ranked),
        "items": weekly_ranked[:TOP_N],
        "items_all": weekly_ranked,
    }

    DATA.mkdir(parents=True, exist_ok=True)
    DAILY.mkdir(parents=True, exist_ok=True)
    LATEST.write_text(json.dumps(latest, ensure_ascii=False, indent=2), encoding="utf-8")
    WEEKLY.write_text(json.dumps(weekly, ensure_ascii=False, indent=2), encoding="utf-8")
    (DAILY / f"{today}.json").write_text(
        json.dumps({"daily": latest, "weekly": weekly}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    update_history(now, daily_ranked, weekly_ranked)
    return latest


# --------------------------------------------------------------------------
# 逐日星数快照（折线图用）
# --------------------------------------------------------------------------
def update_history(now: datetime, daily_ranked: list, weekly_ranked: list):
    hist = {"note": "", "unit": "stars", "days": {}}
    if HISTORY.exists():
        try:
            hist = json.loads(HISTORY.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
    days = hist.setdefault("days", {})

    # 合并而不是覆盖：已有仓库保留，新出现的补上，避免同一天重跑丢数据。
    # 日榜和周榜的候选都要记：只在日榜出现的项目（某天突然爆火、7 天累计还不够进周榜）
    # 当天不记的话，第二天就查不到「昨日星数」，只能显示「无昨日数据」。
    bucket = days.setdefault(now.date().isoformat(), {})
    for it in list(daily_ranked) + list(weekly_ranked):
        if not it.get("stars"):
            continue
        bucket.setdefault(it["repo"], it["stars"])

    # 只保留最近 HISTORY_DAYS 天
    keep = sorted(days)[-HISTORY_DAYS:]
    hist["days"] = {d: days[d] for d in keep}
    hist["note"] = (
        "逐日实测总星数快照（来自 GitHub Trending 日榜 + 周榜页面读数）。"
        "每天运行后追加一天，用于计算「今日涨幅」与折线图。"
    )
    HISTORY.write_text(json.dumps(hist, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  · 逐日快照已更新：{len(hist['days'])} 天（{', '.join(sorted(hist['days']))}）", flush=True)


# --------------------------------------------------------------------------
# 推送
# --------------------------------------------------------------------------
def load_config() -> dict:
    if CONFIG.exists():
        try:
            return json.loads(CONFIG.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            print(f"  ! config.json 读取失败: {exc}", flush=True)
    return {}


def push_bark(cfg: dict, result: dict) -> str:
    base = (cfg.get("bark_url") or "").strip().rstrip("/")
    if not base:
        return "未配置 Bark，跳过"
    items = result["items"]
    if not items:
        return "榜单为空，跳过通知"
    title = "GitHub 今日涨星 Top10"
    body = "  ".join(f"{it['rank']}. {it['name']} +{it['gain']:,}" for it in items[:3])
    url = f"{base}/{urllib.parse.quote(title)}/{urllib.parse.quote(body)}"
    params = {"group": "GitHub榜单"}
    if cfg.get("page_url"):
        params["url"] = cfg["page_url"]
    url += "?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=20) as resp:
            resp.read()
        return "Bark 通知已发送"
    except Exception as exc:  # noqa: BLE001
        return f"Bark 通知失败: {exc}"


def build_mail_html(daily: dict, weekly: dict, page_url: str = "") -> str:
    def rows(items, label):
        out = []
        for it in items:
            desc = htmllib.escape(it["description"] or "暂无描述")
            out.append(f"""
        <tr>
          <td style="padding:13px 8px;width:34px;text-align:center;color:#9aa3b2;font-size:17px;font-weight:700;border-bottom:1px solid #eef0f4">{it['rank']}</td>
          <td style="padding:13px 8px;border-bottom:1px solid #eef0f4">
            <a href="{it['url']}" style="color:#1a56db;font-size:15px;font-weight:600;text-decoration:none">{htmllib.escape(it['repo'])}</a>
            <div style="color:#6b7280;font-size:12.5px;margin-top:3px;line-height:1.5">{desc}</div>
            <div style="color:#9aa3b2;font-size:11.5px;margin-top:5px">{htmllib.escape(it['language'] or '—')} · 总星 {it['stars']:,}</div>
          </td>
          <td style="padding:13px 8px;width:88px;text-align:right;color:#d92d20;font-size:16px;font-weight:700;white-space:nowrap;border-bottom:1px solid #eef0f4">+{it['gain']:,}</td>
        </tr>""")
        return "".join(out)

    link = (
        f'<p style="margin:22px 0 0"><a href="{page_url}" style="background:#1a56db;color:#fff;padding:10px 18px;border-radius:8px;text-decoration:none;font-size:14px">在 iPad 上打开榜单</a></p>'
        if page_url else ""
    )
    return f"""<!doctype html><html><body style="margin:0;padding:24px;background:#f6f7f9;font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif">
<div style="max-width:640px;margin:0 auto;background:#fff;border-radius:14px;padding:26px;box-shadow:0 2px 12px rgba(0,0,0,.05)">
  <div style="font-size:21px;font-weight:700;color:#111827">GitHub 今日涨星 Top10</div>
  <div style="color:#6b7280;font-size:12.5px;margin-top:5px">{daily['date']} · 数据来自 GitHub 官方 Trending 日榜 · 按 stars today 降序</div>
  <table style="width:100%;border-collapse:collapse;margin-top:16px">{rows(daily['items'], '今日')}</table>
  <div style="font-size:17px;font-weight:700;color:#111827;margin-top:28px">近 7 天涨星 Top10</div>
  <div style="color:#6b7280;font-size:12.5px;margin-top:5px">数据来自 GitHub 官方 Trending 周榜 · 按 stars this week 降序</div>
  <table style="width:100%;border-collapse:collapse;margin-top:12px">{rows(weekly['items'], '7天')}</table>
  {link}
  <p style="color:#9aa3b2;font-size:11.5px;margin-top:24px;line-height:1.6">说明：Trending 页面只包含约 25 个 GitHub 筛选过的候选仓库，不是全站全量；页面原顺序含加权，本邮件是按涨星数重新降序后的结果。</p>
</div></body></html>"""


def send_mail(cfg: dict, daily: dict, weekly: dict) -> str:
    email = cfg.get("email") or {}
    if not email.get("password") or not email.get("sender"):
        return "未配置邮箱，跳过"
    if not daily["items"]:
        return "榜单为空，跳过邮件"

    plain_lines = [f"GitHub 今日涨星 Top10 · {daily['date']}", ""]
    for it in daily["items"]:
        plain_lines.append(f"{it['rank']}. +{it['gain']:,}  {it['repo']}  (总 {it['stars']:,})")
        plain_lines.append(f"   {it['url']}")
    plain_lines += ["", "近 7 天涨星 Top10", ""]
    for it in weekly["items"]:
        plain_lines.append(f"{it['rank']}. +{it['gain']:,}  {it['repo']}  (总 {it['stars']:,})")
        plain_lines.append(f"   {it['url']}")
    plain = "\n".join(plain_lines)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = Header(f"GitHub 涨星榜 {daily['date']}（今日 + 近7天）", "utf-8")
    msg["From"] = email["sender"]
    msg["To"] = email.get("receiver") or email["sender"]
    msg.attach(MIMEText(plain, "plain", "utf-8"))
    msg.attach(MIMEText(build_mail_html(daily, weekly, cfg.get("page_url", "")), "html", "utf-8"))
    try:
        with smtplib.SMTP_SSL(
            email.get("smtp_host", "smtp.qq.com"),
            int(email.get("smtp_port", 465)),
            timeout=25,
        ) as server:
            server.login(email["sender"], email["password"])
            server.sendmail(msg["From"], [msg["To"]], msg.as_string())
        return "邮件已发送"
    except Exception as exc:  # noqa: BLE001
        return f"邮件发送失败: {exc}"


def load_weekly() -> dict:
    if WEEKLY.exists():
        try:
            return json.loads(WEEKLY.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
    return {"date": "", "items": []}


def print_summary(result: dict, weekly: dict):
    print("\n" + "=" * 64)
    print(f"GitHub 今日涨星 Top10  ·  {result['date']}")
    print(f"来源：GitHub 官方 Trending 日榜 stars today（共 {result['candidate_count']} 个候选）")
    print("=" * 64)
    for it in result["items"]:
        print(f"{it['rank']:>2}. +{it['gain']:<7,} {it['repo']:<42} 总 {it['stars']:>8,}")
        if it["description"]:
            print(f"     {it['description'][:86]}")
    print("-" * 64)
    print(f"近 7 天涨星 Top10  ·  {weekly.get('date', '')}")
    print("-" * 64)
    for it in weekly.get("items", []):
        print(f"{it['rank']:>2}. +{it['gain']:<7,} {it['repo']:<42} 总 {it['stars']:>8,}")
    print("=" * 64 + "\n")


def main():
    no_push = "--no-push" in sys.argv
    from_raw = "--from-raw" in sys.argv
    now = datetime.now(timezone.utc).astimezone()
    print(f"开始抓取：{now.strftime('%Y-%m-%d %H:%M:%S %z')}"
          f"{'（--from-raw 读原文）' if from_raw else ''}", flush=True)
    daily = collect(now, from_raw=from_raw)
    weekly = load_weekly()
    print_summary(daily, weekly)

    if no_push:
        print("（--no-push：本次不发送通知与邮件）")
        return

    cfg = load_config()
    print("推送状态：", flush=True)
    print("  ·", push_bark(cfg, daily), flush=True)
    print("  ·", send_mail(cfg, daily, weekly), flush=True)


if __name__ == "__main__":
    main()
