"""Render analyses to HTML and write an RSS 2.0 feed."""

from datetime import datetime, timezone
from email.utils import format_datetime
from html import escape
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

from .llm import ANALYSIS_SECTIONS

HEADINGS = {
    "background": "研究背景",
    "findings": "核心发现",
    "methods": "方法亮点",
    "why_interesting": "为什么值得读",
    "caveats": "局限与疑问",
}


def render_item(paper, reason: str, analysis: dict) -> dict:
    authors = ", ".join(paper.authors[:6]) + (" et al." if len(paper.authors) > 6 else "")
    parts = [
        f"<p><strong>{escape(analysis['one_liner'])}</strong></p>",
        f"<p><em>{escape(paper.title)}</em><br/>{escape(paper.journal)} · {escape(paper.pub_date)}<br/>{escape(authors)}</p>",
        f"<blockquote><p>入选理由：{escape(reason)}</p></blockquote>",
    ]
    for key in ANALYSIS_SECTIONS:
        if key == "one_liner":
            continue
        paras = [p.strip() for p in analysis[key].split("\n\n") if p.strip()]
        parts.append(f"<h3>{HEADINGS[key]}</h3>")
        parts.extend(f"<p>{escape(p)}</p>" for p in paras)
    source = "PMC 开放获取全文" if paper.full_text else "PubMed 摘要"
    parts.append(
        f'<hr/><p>解读依据：{source} · 由 Claude 生成，关键结论请以原文为准<br/>'
        f'原文：<a href="{escape(paper.url)}">{escape(paper.url)}</a> · PMID {paper.pmid}</p>'
    )
    return {
        "pmid": paper.pmid,
        "title": analysis["title_zh"],
        "link": paper.url,
        "journal": paper.journal,
        "published": format_datetime(datetime.now(timezone.utc)),
        "html": "\n".join(parts),
    }


def write_rss(items: list[dict], cfg: dict, path: Path) -> None:
    site = cfg["site_url"].rstrip("/") + "/"
    entries = []
    for it in items:
        html = it["html"].replace("]]>", "]]]]><![CDATA[>")
        entries.append(f"""    <item>
      <title>{xml_escape(it['title'])}</title>
      <link>{xml_escape(it['link'])}</link>
      <guid isPermaLink="false">pubmed-{it['pmid']}</guid>
      <category>{xml_escape(it['journal'])}</category>
      <pubDate>{it['published']}</pubDate>
      <description><![CDATA[{html}]]></description>
      <content:encoded><![CDATA[{html}]]></content:encoded>
    </item>""")
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/" xmlns:atom="http://www.w3.org/2005/Atom">
  <channel>
    <title>{xml_escape(cfg['title'])}</title>
    <link>{xml_escape(site)}</link>
    <atom:link href="{xml_escape(site)}feed.xml" rel="self" type="application/rss+xml"/>
    <description>{xml_escape(cfg['description'])}</description>
    <language>zh-cn</language>
    <lastBuildDate>{format_datetime(datetime.now(timezone.utc))}</lastBuildDate>
{chr(10).join(entries)}
  </channel>
</rss>
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(xml, encoding="utf-8")
