"""Re-download figures of already published items using the current [figures] config — no LLM calls.

    python refresh_figures.py

Use it after changing keep_original / max_width / quality / grayscale. The text of each digest is left as is;
only the image files under docs/figures/<pmid>/ and the image URLs inside the stored HTML change.
"""

import json
import re
import sys
import tomllib

from digest.feed import write_rss
from digest.figures import FigureStore
from digest.pubmed import PubMed
from run import FEED_PATH, FIGURES_DIR, ITEMS_PATH, ROOT, WEB_ITEMS_PATH


def main() -> int:
    cfg = tomllib.loads((ROOT / "config.toml").read_text(encoding="utf-8"))
    fig_cfg = cfg["figures"]
    store = FigureStore(FIGURES_DIR, cfg["feed"]["site_url"], fig_cfg["max_width"], fig_cfg["grayscale"],
                        fig_cfg["keep_original"], fig_cfg["quality"], fig_cfg["sharpen"],
                        fig_cfg["gray_gamma"])
    pubmed = PubMed(**cfg["ncbi"])
    items = json.loads(ITEMS_PATH.read_text(encoding="utf-8"))

    for item in items:
        pmid = item["pmid"]
        pattern = re.compile(rf'https?://[^"\s]*?/figures/{pmid}/([A-Za-z0-9_-]+)\.(?:jpe?g|webp|png|gif)')
        old_urls = {m.group(1): m.group(0) for m in pattern.finditer(item["html"])}
        if not old_urls:
            continue
        print(f"[{pmid}] {len(old_urls)} 张图")

        paper = pubmed.fetch([pmid])[0]
        pubmed.link_pmc([paper])
        if not paper.pmcid:
            print("  ! 找不到 PMCID，保留原图片")
            continue
        pubmed.fetch_pmc(paper)
        store.prepare(paper)
        wanted = [f.id for f in paper.figures if re.sub(r"[^A-Za-z0-9_-]", "_", f.id) in old_urls]
        store.download(paper, wanted)

        new_names = set()
        for fig in paper.figures:
            stem = re.sub(r"[^A-Za-z0-9_-]", "_", fig.id)
            if fig.image_url and stem in old_urls:
                item["html"] = item["html"].replace(old_urls[stem], fig.image_url)
                new_names.add(fig.image_url.rsplit("/", 1)[1])
                print(f"  {stem}: {old_urls[stem].rsplit('/', 1)[1]} → {fig.image_url.rsplit('/', 1)[1]}")
        for f in (FIGURES_DIR / pmid).iterdir():
            if f.name not in new_names and f.name.rsplit(".", 1)[0] in {n.rsplit(".", 1)[0] for n in new_names}:
                f.unlink()  # 同一张图的旧格式文件

    payload = json.dumps(items, ensure_ascii=False, indent=2)
    ITEMS_PATH.write_text(payload, encoding="utf-8")
    WEB_ITEMS_PATH.write_text(payload, encoding="utf-8")
    write_rss(items, cfg["feed"], FEED_PATH)
    print("已更新 items.json、feed.xml 和解读页面")
    return 0


if __name__ == "__main__":
    sys.exit(main())
