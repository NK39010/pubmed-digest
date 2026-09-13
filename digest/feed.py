"""Render analyses to HTML and write an RSS 2.0 feed."""

import re
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


def _paragraphs(text: str) -> list[str]:
    return [f"<p>{escape(p.strip())}</p>" for p in str(text).split("\n\n") if p.strip()]


def _figure_name(label: str) -> str:
    if m := re.search(r"(\d+)", label):
        return f"图 {m.group(1)}"
    return "图形摘要" if "graphical" in label.lower() else escape(label.rstrip("."))


def _render_figure(fig, entry: dict) -> list[str]:
    name = _figure_name(fig.label)
    return [
        f"<h4>{name}｜{escape(str(entry.get('title_zh', '')))}</h4>",
        f'<p><img src="{escape(fig.image_url)}" alt="{name}" style="max-width:100%;height:auto"/></p>',
        f"<p><strong>图例：</strong>{escape(str(entry.get('caption_zh', '')))}</p>",
        *[p.replace("<p>", "<p><strong>图解：</strong>", 1) if i == 0 else p
          for i, p in enumerate(_paragraphs(entry.get("explanation", "")))],
    ]


def render_item(paper, reason: str, analysis: dict, figure_layout: str = "inline") -> dict:
    authors = ", ".join(paper.authors[:6]) + (" et al." if len(paper.authors) > 6 else "")
    parts = [
        f"<p><strong>{escape(analysis['one_liner'])}</strong></p>",
        f"<p><em>{escape(paper.title)}</em><br/>{escape(paper.journal)} · {escape(paper.pub_date)}<br/>{escape(authors)}</p>",
        f"<blockquote><p>入选理由：{escape(reason)}</p></blockquote>",
    ]

    # Keep only figures the model chose that we actually have images for, in paper order.
    figs = {f.id: f for f in paper.figures if f.image_url}
    chosen = [e for e in analysis.get("figures", []) if isinstance(e, dict) and e.get("id") in figs]
    chosen.sort(key=lambda e: list(figs).index(e["id"]))
    sections = [k for k in ANALYSIS_SECTIONS if k != "one_liner"]
    placed = {k: [] for k in sections}
    for e in chosen:
        placed[e.get("place_after") if e.get("place_after") in placed else "findings"].append(e)

    for key in sections:
        parts.append(f"<h3>{HEADINGS[key]}</h3>")
        parts.extend(_paragraphs(analysis[key]))
        if figure_layout == "inline":
            for e in placed[key]:
                parts.extend(_render_figure(figs[e["id"]], e))
        elif key == "findings" and chosen:
            parts.append("<h3>图文解读</h3>")
            for e in chosen:
                parts.extend(_render_figure(figs[e["id"]], e))

    source = "PMC 开放获取全文" if paper.full_text else "PubMed 摘要"
    figure_note = f"<br/>插图来自原文，许可证 {escape(paper.license)}，版权归原作者" if chosen else ""
    parts.append(
        f'<hr/><p>解读依据：{source} · 由 AI 生成，关键结论请以原文为准{figure_note}<br/>'
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
