"""Daily pipeline: PubMed search → LLM picks 1-2 papers → Chinese deep-dive → RSS feed.

    python run.py            # full run (needs DEEPSEEK_API_KEY, see [llm] in config.toml)
    python run.py --dry-run  # only search PubMed and list candidates, no LLM calls
"""

import argparse
import json
import os
import sys
import tomllib
from pathlib import Path

from digest.feed import render_item, write_rss
from digest.pubmed import PubMed

ROOT = Path(__file__).parent
ITEMS_PATH = ROOT / "data" / "items.json"
FEED_PATH = ROOT / "docs" / "feed.xml"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="只检索并打印候选，不调用大模型")
    args = parser.parse_args()

    cfg = tomllib.loads((ROOT / "config.toml").read_text(encoding="utf-8"))
    if repo := os.environ.get("GITHUB_REPOSITORY"):  # running in Actions: owner/name → Pages URL
        owner, name = repo.split("/")
        cfg["feed"]["site_url"] = f"https://{owner.lower()}.github.io/{name}/"
    items = json.loads(ITEMS_PATH.read_text(encoding="utf-8")) if ITEMS_PATH.exists() else []
    seen = {it["pmid"] for it in items}

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

    if args.dry_run:
        for p in papers:
            print(f"  [{p.pmid}] {p.journal} | {p.title[:100]}")
        return 0

    from digest.llm import Curator

    curator = Curator(cfg["llm"])
    s = cfg["selection"]
    picks = curator.select(papers, s["interests"], s["picks_min"], s["picks_max"])
    new_items = []
    for paper, reason in picks:
        print(f"入选 [{paper.pmid}] {paper.title}\n  理由：{reason}")
        if paper.pmcid:
            paper.full_text = pubmed.fetch_full_text(paper.pmcid)
            print(f"  PMC 全文：{'已获取' if paper.full_text else '不可用，使用摘要'}")
        analysis = curator.analyze(paper, reason)
        if analysis:
            new_items.append(render_item(paper, reason, analysis))

    if not new_items:
        print("本次没有生成新条目")
        write_rss(items, cfg["feed"], FEED_PATH)
        return 0
    items = (new_items + items)[: cfg["feed"]["max_items"]]
    ITEMS_PATH.parent.mkdir(parents=True, exist_ok=True)
    ITEMS_PATH.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    write_rss(items, cfg["feed"], FEED_PATH)
    print(f"已写入 {len(new_items)} 篇 → {FEED_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
