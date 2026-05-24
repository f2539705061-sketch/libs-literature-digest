#!/usr/bin/env python3
"""
Cloud-friendly daily LIBS/ED-LIBS literature digest.

Designed for GitHub Actions or any small Linux/Windows server. It searches
public metadata sources, selects five relevant papers, writes a Chinese digest,
and can send it through Gmail SMTP using an app password.
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import re
import smtplib
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from email.message import EmailMessage
from zoneinfo import ZoneInfo


DEFAULT_DAYS = 90
DEFAULT_LIMIT = 5
TIMEZONE = ZoneInfo("Asia/Shanghai")

SEARCH_QUERIES = [
    '"laser-induced breakdown spectroscopy" cadmium soil water',
    '"laser-induced breakdown spectroscopy" heavy metals soil Pb Cr',
    '"electrochemical deposition" "laser-induced breakdown spectroscopy"',
    '"LIBS" "cadmium" enrichment substrate membrane',
    '"laser-induced breakdown spectroscopy" "gate delay" "gate width"',
    '"laser-induced breakdown spectroscopy" Pb Cr water',
    '"laser-induced breakdown spectroscopy" soil Pb Cr machine learning',
    '"superhydrophobic" "laser-induced breakdown spectroscopy" heavy metals',
    '"cadmium" "laser-induced breakdown spectroscopy" "Sensors and Actuators B"',
]

USER_AGENT = (
    "libs-ed-libs-literature-digest/1.0 "
    "(mailto:f2539705061@gmail.com; public metadata daily digest)"
)


@dataclass
class Article:
    title: str
    venue: str
    date: str
    doi: str
    url: str
    abstract: str
    source: str
    status: str
    score: float = 0.0


def load_dotenv(path: str = ".env") -> None:
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)


def request_json(url: str, headers: dict[str, str] | None = None, retries: int = 2) -> dict:
    merged_headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if headers:
        merged_headers.update(headers)
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=merged_headers)
            with urllib.request.urlopen(req, timeout=25) as response:
                return json.loads(response.read().decode("utf-8", "replace"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"Failed to fetch {url}: {last_error}")


def clean_text(value: object) -> str:
    if not value:
        return ""
    if isinstance(value, list):
        value = " ".join(str(x) for x in value if x)
    text = str(value)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def normalize_doi(doi: str) -> str:
    doi = (doi or "").strip().lower()
    doi = doi.removeprefix("https://doi.org/")
    doi = doi.removeprefix("http://doi.org/")
    doi = doi.removeprefix("doi:")
    return doi


def title_key(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", title.lower())[:120]


def parse_crossref_date(item: dict) -> str:
    for key in ("published-online", "published-print", "published", "issued"):
        parts = item.get(key, {}).get("date-parts")
        if parts and parts[0]:
            values = parts[0]
            year = values[0]
            month = values[1] if len(values) > 1 else 1
            day = values[2] if len(values) > 2 else 1
            return f"{year:04d}-{month:02d}-{day:02d}"
    return ""


def openalex_abstract(index: dict | None) -> str:
    if not index:
        return ""
    words: list[tuple[int, str]] = []
    for word, positions in index.items():
        for pos in positions:
            words.append((int(pos), word))
    return " ".join(word for _, word in sorted(words))


def fetch_crossref(from_date: str) -> list[Article]:
    articles: list[Article] = []
    for query in SEARCH_QUERIES:
        params = {
            "query.bibliographic": query,
            "filter": f"from-pub-date:{from_date},type:journal-article",
            "sort": "published",
            "order": "desc",
            "rows": "20",
        }
        url = "https://api.crossref.org/works?" + urllib.parse.urlencode(params)
        try:
            data = request_json(url)
        except RuntimeError as exc:
            print(f"[warn] Crossref query failed: {exc}", file=sys.stderr)
            continue
        for item in data.get("message", {}).get("items", []):
            title = clean_text(item.get("title"))
            if not title:
                continue
            doi = normalize_doi(item.get("DOI", ""))
            articles.append(
                Article(
                    title=title,
                    venue=clean_text(item.get("container-title")) or "Unknown venue",
                    date=parse_crossref_date(item),
                    doi=doi,
                    url=f"https://doi.org/{doi}" if doi else clean_text(item.get("URL")),
                    abstract=clean_text(item.get("abstract")),
                    source="Crossref",
                    status="journal article / indexed metadata",
                )
            )
    return articles


def fetch_openalex(from_date: str) -> list[Article]:
    articles: list[Article] = []
    for query in SEARCH_QUERIES:
        params = {
            "search": query,
            "filter": f"from_publication_date:{from_date}",
            "sort": "publication_date:desc",
            "per-page": "20",
        }
        url = "https://api.openalex.org/works?" + urllib.parse.urlencode(params)
        try:
            data = request_json(url)
        except RuntimeError as exc:
            print(f"[warn] OpenAlex query failed: {exc}", file=sys.stderr)
            continue
        for item in data.get("results", []):
            title = clean_text(item.get("title"))
            if not title:
                continue
            doi = normalize_doi(item.get("doi", ""))
            primary_location = item.get("primary_location") or {}
            source = primary_location.get("source") or {}
            venue = clean_text(source.get("display_name"))
            articles.append(
                Article(
                    title=title,
                    venue=venue or "Unknown venue",
                    date=clean_text(item.get("publication_date")),
                    doi=doi,
                    url=f"https://doi.org/{doi}" if doi else clean_text(item.get("id")),
                    abstract=clean_text(openalex_abstract(item.get("abstract_inverted_index"))),
                    source="OpenAlex",
                    status=clean_text(item.get("type_crossref")) or "public metadata",
                )
            )
    return articles


def fetch_semantic_scholar(from_date: str) -> list[Article]:
    articles: list[Article] = []
    fields = "title,abstract,year,publicationDate,venue,url,externalIds,publicationTypes"
    api_key = os.getenv("SEMANTIC_SCHOLAR_API_KEY", "").strip()
    if not api_key:
        print("[info] SEMANTIC_SCHOLAR_API_KEY not set; skipping Semantic Scholar", file=sys.stderr)
        return articles
    headers: dict[str, str] = {"x-api-key": api_key}
    for query in SEARCH_QUERIES:
        params = {"query": query, "limit": "20", "fields": fields}
        url = "https://api.semanticscholar.org/graph/v1/paper/search?" + urllib.parse.urlencode(params)
        try:
            data = request_json(url, headers=headers, retries=1)
        except RuntimeError as exc:
            print(f"[warn] Semantic Scholar query failed: {exc}", file=sys.stderr)
            continue
        for item in data.get("data", []):
            pub_date = clean_text(item.get("publicationDate"))
            if pub_date and pub_date < from_date:
                continue
            doi = normalize_doi((item.get("externalIds") or {}).get("DOI", ""))
            articles.append(
                Article(
                    title=clean_text(item.get("title")),
                    venue=clean_text(item.get("venue")) or "Unknown venue",
                    date=pub_date or str(item.get("year") or ""),
                    doi=doi,
                    url=f"https://doi.org/{doi}" if doi else clean_text(item.get("url")),
                    abstract=clean_text(item.get("abstract")),
                    source="Semantic Scholar",
                    status=", ".join(item.get("publicationTypes") or []) or "public metadata",
                )
            )
    return articles


def has_libs_signal(text: str) -> bool:
    return bool(
        "laser-induced breakdown spectroscopy" in text
        or "laser induced breakdown spectroscopy" in text
        or "breakdown spectroscopy" in text
        or re.search(r"\blibs\b", text)
    )


def has_research_focus(text: str) -> bool:
    element = bool(
        re.search(
            r"\b(cadmium|chromium|lead|copper|zinc|heavy metal|toxic element|trace element)\b",
            text,
            re.I,
        )
        or re.search(r"\b(cd|cr|pb|cu|zn)(?:\b|[0-9+])", text, re.I)
    )
    matrix = bool(re.search(r"\b(soil|water|aqueous|wastewater|contaminated|environmental)\b", text, re.I))
    route = bool(
        re.search(
            r"\b(electrochemical|deposition|enrichment|substrate|membrane|electrode|nanoparticle|nanosheet|coffee-ring|superhydrophobic)\b",
            text,
            re.I,
        )
    )
    return element or (matrix and route)


def score_article(article: Article) -> float:
    text = f"{article.title} {article.abstract}".lower()
    score = 0.0

    if "laser-induced breakdown spectroscopy" in text:
        score += 16
    if re.search(r"\blibs\b", text):
        score += 8
    if "breakdown spectroscopy" in text:
        score += 5

    element_patterns = [
        r"\bcadmium\b", r"\bcd(?:\b|[0-9+])", r"\bchromium\b", r"\bcr(?:\b|[0-9+])",
        r"\blead\b", r"\bpb(?:\b|[0-9+])", r"\bcopper\b", r"\bcu(?:\b|[0-9+])",
        r"\bzinc\b", r"\bzn(?:\b|[0-9+])",
    ]
    for pattern in element_patterns:
        if re.search(pattern, text):
            score += 4

    for term in ("soil", "water", "aqueous", "heavy metal", "trace element"):
        if term in text:
            score += 3

    for term in ("electrochemical", "deposition", "enrichment", "substrate", "membrane", "electrode", "nanoparticle"):
        if term in text:
            score += 4

    for term in ("gate delay", "gate width", "iccd", "machine learning", "quantitative", "limit of detection", "lod"):
        if term in text:
            score += 2.5

    try:
        published = dt.date.fromisoformat(article.date[:10])
        age_days = max(0, (dt.datetime.now(TIMEZONE).date() - published).days)
        score += max(0, 8 - age_days / 12)
    except ValueError:
        pass

    if "review" in text:
        score -= 2
    if not has_libs_signal(text):
        score -= 12
    return score


def is_article_relevant(article: Article) -> bool:
    text = f"{article.title} {article.abstract}"
    lowered = text.lower()
    if not has_libs_signal(lowered):
        return False
    if not has_research_focus(text):
        return False
    weak_domains = ("nuclear fuel", "phosphoric acid", "geoscience", "mineralogy", "petrology")
    if any(term in lowered for term in weak_domains) and not re.search(
        r"\b(cadmium|chromium|lead|copper|heavy metal|soil|water|aqueous|electrochemical|enrichment)\b",
        lowered,
    ):
        return False
    return True


def dedupe_and_rank(articles: list[Article], limit: int) -> list[Article]:
    deduped: dict[str, Article] = {}
    for article in articles:
        if not article.title:
            continue
        if not is_article_relevant(article):
            continue
        article.score = score_article(article)
        if article.score < 18:
            continue
        key = normalize_doi(article.doi) or title_key(article.title)
        existing = deduped.get(key)
        if not existing or article.score > existing.score or (article.abstract and not existing.abstract):
            deduped[key] = article
    return sorted(deduped.values(), key=lambda item: (item.score, item.date), reverse=True)[:limit]


def detect_elements(text: str) -> str:
    pairs = [
        ("Cd", r"\b(cd|cadmium)\b"),
        ("Cr", r"\b(cr|chromium)\b"),
        ("Pb", r"\b(pb|lead)\b"),
        ("Cu", r"\b(cu|copper)\b"),
        ("Zn", r"\b(zn|zinc)\b"),
    ]
    found = [label for label, pattern in pairs if re.search(pattern, text, re.I)]
    return "/".join(found) if found else "重金属/痕量元素"


def detect_matrix(text: str) -> str:
    if re.search(r"\bsoil\b", text, re.I):
        return "土壤"
    if re.search(r"\bwater|aqueous|solution\b", text, re.I):
        return "水体或液体样品"
    if re.search(r"\bplant|leaf|tea\b", text, re.I):
        return "植物或复杂实际样品"
    return "复杂样品体系"


def detect_method(text: str) -> str:
    lowered = text.lower()
    if "electrochemical" in lowered or "deposition" in lowered:
        return "电化学沉积/富集与 LIBS 联用"
    if "substrate" in lowered or "nanoparticle" in lowered or "membrane" in lowered:
        return "增强基底或纳米材料辅助 LIBS"
    if "machine learning" in lowered or "random forest" in lowered or "neural" in lowered:
        return "机器学习辅助 LIBS 定量"
    if "gate delay" in lowered or "gate width" in lowered or "iccd" in lowered:
        return "光谱采集参数优化"
    return "LIBS 定量分析"


def article_intro(article: Article) -> str:
    text = f"{article.title}. {article.abstract}"
    elements = detect_elements(text)
    matrix = detect_matrix(text)
    method = detect_method(text)

    metric_note = "摘要或元数据中未必给出完整性能指标，因此应优先查看原文确认检测限、RSD、线性范围和验证样品。"
    if re.search(r"\bLOD\b|limit of detection|detection limit", text, re.I):
        metric_note = "文中涉及检测限或灵敏度指标，阅读时应重点核对 LOD 的单位、计算方式和真实样品验证。"
    if re.search(r"\bRSD\b|repeatability|reproduc", text, re.I):
        metric_note = "文中涉及重复性或稳定性评价，适合对照你目前关注的 shot-to-shot 波动和定量可靠性。"

    return (
        f"这篇文献聚焦{matrix}中的{elements}检测，方法路线为{method}。"
        f"它对你的 ED-LIBS/重金属检测课题的价值在于，可以帮助判断样品富集、基底形貌、采集参数或建模策略中哪一环最可能限制灵敏度和稳定性。"
        f"{metric_note}可迁移启发是：不要只看谱线是否增强，还要同时检查沉积分布、基体效应、重复性和跨样品泛化能力，避免把单一条件下的好结果误写成可直接应用的方法。"
    )


def compose_digest(articles: list[Article], run_date: dt.date, days: int) -> str:
    lines: list[str] = []
    lines.append(f"# LIBS/ED-LIBS 每日文献简报 - {run_date.isoformat()}")
    lines.append("")
    lines.append(
        f"检索口径：公开源组合检索 Crossref、OpenAlex，并在配置 API key 时包含 Semantic Scholar；"
        f"优先最新发表、online first 和 early access。若最新结果不足，则回溯最近 {days} 天。"
    )
    lines.append("")
    if articles:
        lines.append(
            f"今日结论：筛选出 {len(articles)} 篇与 LIBS/ED-LIBS 重金属检测最相关的文献。"
            "排序优先考虑 Cd/Cr/Pb/Cu、土壤/水体、富集基底、门延迟/门宽优化和机器学习定量。"
        )
    else:
        lines.append(
            "今日结论：在当前检索范围内没有找到足够高相关的新文献。宁可空缺，也不使用低相关泛 LIBS 论文凑数。"
        )
    lines.append("")
    heading = "## 今日五篇重点文献" if len(articles) == 5 else f"## 今日重点文献（{len(articles)} 篇）"
    lines.append(heading)
    lines.append("")

    for idx, article in enumerate(articles, 1):
        doi_or_url = f"https://doi.org/{article.doi}" if article.doi else article.url
        lines.append(f"### {idx}. {article.title}")
        lines.append("")
        lines.append(f"- 来源：{article.venue}")
        lines.append(f"- 日期：{article.date or '未标明'}")
        lines.append(f"- 状态：{article.status}")
        lines.append(f"- DOI/链接：{doi_or_url or '未找到稳定链接'}")
        lines.append(f"- 元数据来源：{article.source}")
        lines.append("")
        lines.append(article_intro(article))
        lines.append("")

    lines.append("## 备注")
    lines.append("")
    lines.append(
        "本简报基于公开元数据、摘要和 DOI/期刊页面可见信息生成；除非正文明确说明已读取全文，否则不等同于全文精读。"
        "检测限相关表述统一按“更低检测限/更高灵敏度/更好检测能力”的方向理解。"
    )
    return "\n".join(lines).strip() + "\n"


def linkify(text: str) -> str:
    return re.sub(r"(https?://[^\s<]+)", r'<a href="\1">\1</a>', text)


def markdown_to_html(markdown: str) -> str:
    body: list[str] = []
    in_list = False
    for raw in markdown.splitlines():
        line = raw.rstrip()
        if not line:
            if in_list:
                body.append("</ul>")
                in_list = False
            continue
        if line.startswith("# "):
            if in_list:
                body.append("</ul>")
                in_list = False
            body.append(f"<h1>{html.escape(line[2:])}</h1>")
        elif line.startswith("## "):
            if in_list:
                body.append("</ul>")
                in_list = False
            body.append(f"<h2>{html.escape(line[3:])}</h2>")
        elif line.startswith("### "):
            if in_list:
                body.append("</ul>")
                in_list = False
            body.append(f"<h3>{html.escape(line[4:])}</h3>")
        elif line.startswith("- "):
            if not in_list:
                body.append("<ul>")
                in_list = True
            body.append(f"<li>{linkify(html.escape(line[2:]))}</li>")
        else:
            if in_list:
                body.append("</ul>")
                in_list = False
            body.append(f"<p>{linkify(html.escape(line))}</p>")
    if in_list:
        body.append("</ul>")
    return (
        "<!doctype html><html><body style=\"font-family:Arial,'Microsoft YaHei',sans-serif;"
        "line-height:1.7;color:#17211b;max-width:820px;margin:0 auto;padding:24px;\">"
        + "\n".join(body)
        + "</body></html>"
    )


def send_email(subject: str, markdown_body: str) -> None:
    smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.getenv("SMTP_PORT", "465"))
    username = os.getenv("SMTP_USERNAME", "").strip()
    password = os.getenv("SMTP_PASSWORD", "").strip()
    recipient = os.getenv("DIGEST_TO", username).strip()
    sender_name = os.getenv("DIGEST_FROM_NAME", "LIBS Literature Digest").strip()

    missing = [name for name, value in {
        "SMTP_USERNAME": username,
        "SMTP_PASSWORD": password,
        "DIGEST_TO": recipient,
    }.items() if not value]
    if missing:
        raise RuntimeError(f"Missing required email environment variables: {', '.join(missing)}")

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = f"{sender_name} <{username}>"
    message["To"] = recipient
    message.set_content(markdown_body, subtype="plain", charset="utf-8")
    message.add_alternative(markdown_to_html(markdown_body), subtype="html", charset="utf-8")

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(smtp_host, smtp_port, context=context, timeout=30) as server:
        server.login(username, password)
        server.send_message(message)


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Generate and send LIBS/ED-LIBS literature digest.")
    parser.add_argument("--days", type=int, default=int(os.getenv("LOOKBACK_DAYS", DEFAULT_DAYS)))
    parser.add_argument("--limit", type=int, default=int(os.getenv("DIGEST_LIMIT", DEFAULT_LIMIT)))
    parser.add_argument("--send", action="store_true", help="Send email through SMTP.")
    parser.add_argument("--dry-run", action="store_true", help="Print digest without sending.")
    parser.add_argument("--output", default=os.getenv("DIGEST_OUTPUT", ""), help="Optional markdown output path.")
    args = parser.parse_args()

    run_date = dt.datetime.now(TIMEZONE).date()
    from_date = (run_date - dt.timedelta(days=args.days)).isoformat()

    articles = []
    articles.extend(fetch_crossref(from_date))
    articles.extend(fetch_openalex(from_date))
    articles.extend(fetch_semantic_scholar(from_date))
    selected = dedupe_and_rank(articles, args.limit)
    digest = compose_digest(selected, run_date, args.days)

    if args.output:
        output_path = os.path.abspath(args.output)
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(digest)
        print(f"[info] wrote {output_path}", file=sys.stderr)

    if args.send and not args.dry_run:
        send_email(f"LIBS/ED-LIBS 每日文献简报 - {run_date.isoformat()}", digest)
        print("[info] email sent", file=sys.stderr)
    else:
        print(digest)
        print("[info] dry run only; email not sent", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
