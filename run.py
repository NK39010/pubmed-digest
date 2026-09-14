"""Daily pipeline: PubMed search → LLM picks 1-2 papers → Chinese deep-dive → RSS feed.

    python run.py            # full run (needs DEEPSEEK_API_KEY, see [llm] in config.toml)
    python run.py --dry-run  # only search PubMed and list candidates, no LLM calls
"""

import argparse
import json
import os
import sys
import tomllib
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

from digest.feed import render_item, write_rss
from digest.figures import FigureStore
from digest.pubmed import PubMed

ROOT = Path(__file__).parent
ITEMS_PATH = ROOT / "data" / "items.json"
FEED_PATH = ROOT / "docs" / "feed.xml"
FIGURES_DIR = ROOT / "docs" / "figures"
WEB_ITEMS_PATH = ROOT / "docs" / "items.json"  # same content as data/items.json, for the web viewer


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="只检索并打印候选，不调用大模型")
    parser.add_argument("--once-per-day", action="store_true", help="北京时间今天已经产出过就跳过（定时任务的备用触发用）")
    args = parser.parse_args()

    cfg = tomllib.loads((ROOT / "config.toml").read_text(encoding="utf-8"))
    if repo := os.environ.get("GITHUB_REPOSITORY"):  # running in Actions: owner/name → Pages URL
        owner, name = repo.split("/")
        cfg["feed"]["site_url"] = f"https://{owner.lower()}.github.io/{name}/"
    items = json.loads(ITEMS_PATH.read_text(encoding="utf-8")) if ITEMS_PATH.exists() else []
    seen = {it["pmid"] for it in items}

    if args.once_per_day:
        # 一"期"从北京时间 06:00 算起，凌晨手动跑出来的内容归到前一天
        digest_day = lambda t: (t.astimezone(timezone(timedelta(hours=8))) - timedelta(hours=6)).date()
        today = digest_day(datetime.now(timezone.utc))
        if any(digest_day(parsedate_to_datetime(it["published"])) == today for it in items):
            print(f"{today} 这一期已经产出过，跳过本次运行")
            return 0

    pubmed = PubMed(**cfg["ncbi"])
    query = PubMed.build_query(cfg["search"])
    pmids = pubmed.search(query, cfg["search"]["days"], cfg["search"]["max_candidates"])
    pmids = [p for p in pmids if p not in seen]
    print(f"PubMed 近 {cfg['search']['days']} 天新候选：{len(pmids)} 篇")
    if not pmids:
        if not args.dry_run:
            write_rss(items, cfg["feed"], FEED_PATH)
        return 0
    papers = [p for p in pubmed.fetch(pmids) if p.abstract]
    pubmed.link_pmc(papers)
    fig_cfg = cfg["figures"]
    store = FigureStore(FIGURES_DIR, cfg["feed"]["site_url"], fig_cfg["max_width"], fig_cfg["grayscale"],
                        fig_cfg["keep_original"], fig_cfg["quality"])
    for p in papers:
        p.open_access = bool(p.pmcid) and store.is_open_access(p.pmcid)
    print(f"其中开放获取（可取全文和原图）：{sum(p.open_access for p in papers)} 篇")

    if args.dry_run:
        for p in papers:
            print(f"  [{p.pmid}]{' [OA]' if p.open_access else ''} {p.journal} | {p.title[:100]}")
        return 0

    from digest.llm import Curator

    curator = Curator(cfg["llm"])
    s = cfg["selection"]
    picks = curator.select(papers, s["interests"], s["picks_min"], s["picks_max"], fig_cfg["prefer_open_access"])
    new_items = []
    for paper, reason in picks:
        print(f"入选 [{paper.pmid}] {paper.title}\n  理由：{reason}")
        if paper.open_access:
            pubmed.fetch_pmc(paper)
            store.prepare(paper)
            print(f"  PMC 全文：{'已获取' if paper.full_text else '不可用'}；可用原图 {len(paper.figures)} 张（{paper.license or '无许可证信息'}）")
        analysis = curator.analyze(paper, reason, fig_cfg["max_figures"])
        if analysis:
            chosen = [e.get("id") for e in analysis.get("figures", []) if isinstance(e, dict)][: fig_cfg["max_figures"]]
            store.download(paper, chosen)
            new_items.append(render_item(paper, reason, analysis, fig_cfg["layout"]))

    if not new_items:
        print("本次没有生成新条目")
        write_rss(items, cfg["feed"], FEED_PATH)
        return 0
    items = (new_items + items)[: cfg["feed"]["max_items"]]
    store.prune({it["pmid"] for it in items})
    ITEMS_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(items, ensure_ascii=False, indent=2)
    ITEMS_PATH.write_text(payload, encoding="utf-8")
    WEB_ITEMS_PATH.write_text(payload, encoding="utf-8")
    write_rss(items, cfg["feed"], FEED_PATH)
    print(f"已写入 {len(new_items)} 篇 → {FEED_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
