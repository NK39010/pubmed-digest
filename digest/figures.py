"""Download figures from the PMC Open Access dataset on AWS and convert them for e-ink reading."""

import io
import re
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path, PurePosixPath

import httpx
from PIL import Image

S3 = "https://pmc-oa-opendata.s3.amazonaws.com"
S3_NS = "{http://s3.amazonaws.com/doc/2006-03-01/}"


KEEPABLE = {".webp", ".jpg", ".jpeg", ".png", ".gif"}  # formats worth serving untouched


class FigureStore:
    def __init__(self, out_dir: Path, site_url: str, max_width: int, grayscale: bool,
                 keep_original: bool = True, quality: int = 92):
        self.out_dir = out_dir
        self.site_url = site_url.rstrip("/") + "/"
        self.max_width = max_width
        self.grayscale = grayscale
        self.keep_original = keep_original
        self.quality = quality
        self.http = httpx.Client(timeout=60, follow_redirects=True)

    def _package(self, pmcid: str) -> tuple[str, list[str]] | None:
        """Latest version folder and its file keys, e.g. ("PMC123.2/", [...])."""
        resp = self.http.get(S3, params={"list-type": "2", "prefix": f"{pmcid}."})
        if resp.status_code != 200:
            return None
        keys = [k.text for k in ET.fromstring(resp.content).iter(f"{S3_NS}Key")]
        versions = {int(m.group(1)) for k in keys if (m := re.match(rf"{pmcid}\.(\d+)/", k))}
        if not versions:
            return None
        folder = f"{pmcid}.{max(versions)}/"
        return folder, [k for k in keys if k.startswith(folder)]

    def is_open_access(self, pmcid: str) -> bool:
        try:
            return self._package(pmcid) is not None
        except (httpx.HTTPError, ET.ParseError):
            return False

    def prepare(self, paper) -> None:
        """Set paper.license and keep only figures whose image exists in the OA package.

        Figures are only kept for Creative Commons licensed articles, since they are republished on a public site.
        """
        if not paper.pmcid or not paper.figures:
            return
        pkg = self._package(paper.pmcid)
        if not pkg:
            return
        folder, keys = pkg
        meta = self.http.get(f"{S3}/{folder}{folder.rstrip('/')}.json")
        if meta.status_code == 200:
            paper.license = meta.json().get("license_code") or ""
        if not paper.license.upper().startswith("CC"):
            paper.figures = []
            return

        by_stem = {PurePosixPath(k).stem: k for k in keys}
        for fig in paper.figures:
            fig.source_key = by_stem.get(PurePosixPath(fig.href).stem, "")
        paper.figures = [f for f in paper.figures if f.source_key]

    def download(self, paper, figure_ids: list[str]) -> None:
        """Download and convert the chosen figures; sets image_url on each one that succeeds."""
        dest = self.out_dir / paper.pmid
        for fig in paper.figures:
            if fig.id not in figure_ids:
                continue
            try:
                resp = self.http.get(f"{S3}/{fig.source_key}")
                resp.raise_for_status()
                dest.mkdir(parents=True, exist_ok=True)
                stem = re.sub(r"[^A-Za-z0-9_-]", "_", fig.id)
                suffix = PurePosixPath(fig.source_key).suffix.lower()
                # 墨水屏阅读器多数支持 WebP，原图直接发布：不缩放、不二次有损编码
                if self.keep_original and suffix in KEEPABLE and not self.grayscale:
                    name = stem + suffix
                    (dest / name).write_bytes(resp.content)
                else:
                    name = stem + ".jpg"
                    self._convert(resp.content, dest / name)
            except (httpx.HTTPError, OSError) as e:
                print(f"  ! 图 {fig.id} 下载失败：{e}")
                continue
            fig.image_url = f"{self.site_url}figures/{paper.pmid}/{name}"

    def _convert(self, data: bytes, path: Path) -> None:
        img = Image.open(io.BytesIO(data))
        if img.mode in ("RGBA", "LA", "P"):
            img = img.convert("RGBA")
            background = Image.new("RGB", img.size, "white")
            background.paste(img, mask=img.getchannel("A"))
            img = background
        img = img.convert("L" if self.grayscale else "RGB")
        if img.width > self.max_width:
            img = img.resize((self.max_width, round(img.height * self.max_width / img.width)), Image.LANCZOS)
        img.save(path, "JPEG", quality=self.quality, optimize=True)

    def prune(self, keep_pmids: set[str]) -> None:
        if not self.out_dir.exists():
            return
        for d in self.out_dir.iterdir():
            if d.is_dir() and d.name not in keep_pmids:
                shutil.rmtree(d)
