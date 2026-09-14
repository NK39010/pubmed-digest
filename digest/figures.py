"""Download figures from the PMC Open Access dataset on AWS and convert them for e-ink reading."""

import io
import re
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path, PurePosixPath

import httpx
from PIL import Image, ImageFilter

S3 = "https://pmc-oa-opendata.s3.amazonaws.com"
S3_NS = "{http://s3.amazonaws.com/doc/2006-03-01/}"


KEEPABLE = {".webp", ".jpg", ".jpeg", ".png", ".gif"}  # formats worth serving untouched


class FigureStore:
    def __init__(self, out_dir: Path, site_url: str, max_width: int, grayscale: bool,
                 keep_original: bool = True, quality: int = 92, sharpen: bool = False,
                 gray_gamma: float = 1.0):
        self.out_dir = out_dir
        self.site_url = site_url.rstrip("/") + "/"
        self.max_width = max_width
        self.grayscale = grayscale
        self.keep_original = keep_original
        self.quality = quality
        self.sharpen = sharpen
        self.gray_gamma = gray_gamma
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

    def _springer_original(self, paper, fig) -> bytes | None:
        """PMC only stores a downscaled web copy (~1600px). Springer-hosted journals (Nature family) also serve
        the figure as uploaded under /full/ — often wider, but not for every figure, so the caller compares sizes.
        A missing file answers 200 with an HTML page, hence the content-type check."""
        name = PurePosixPath(fig.href).stem
        if not paper.doi.startswith("10.1038/") or not re.match(r"\d+_\d+_\d+_Fig\d+_HTML$", name):
            return None
        doi = paper.doi.replace("/", "%2F")
        for ext in ("png", "jpg"):
            try:
                resp = self.http.get(
                    f"https://media.springernature.com/full/springer-static/image/art%3A{doi}/MediaObjects/{name}.{ext}"
                )
            except httpx.HTTPError:
                continue
            if resp.status_code == 200 and resp.headers.get("content-type", "").startswith("image"):
                return resp.content
        return None

    @staticmethod
    def _width(data: bytes) -> int:
        try:
            return Image.open(io.BytesIO(data)).width
        except OSError:
            return 0

    def download(self, paper, figure_ids: list[str]) -> None:
        """Download and convert the chosen figures; sets image_url on each one that succeeds."""
        dest = self.out_dir / paper.pmid
        for fig in paper.figures:
            if fig.id not in figure_ids:
                continue
            try:
                resp = self.http.get(f"{S3}/{fig.source_key}")
                resp.raise_for_status()
                data, suffix = resp.content, PurePosixPath(fig.source_key).suffix.lower()
                hires = self._springer_original(paper, fig)
                if hires and self._width(hires) > self._width(data):
                    data = hires
                    suffix = ".png" if data[:4] == b"\x89PNG" else ".jpg"
                else:
                    hires = None
                dest.mkdir(parents=True, exist_ok=True)
                stem = re.sub(r"[^A-Za-z0-9_-]", "_", fig.id)
                # PNG/JPEG 原样发布；WebP 只有 keep_original 时才原样发布（部分墨水屏阅读器不支持），否则转 JPEG
                passthrough = suffix in KEEPABLE and (suffix != ".webp" or self.keep_original)
                if passthrough and not self.grayscale:
                    name = stem + suffix
                    (dest / name).write_bytes(data)
                else:
                    name = stem + ".jpg"
                    self._convert(data, dest / name)
                if hires is not None:
                    print(f"  图 {fig.id}：用出版商原图（{len(data) // 1024}KB）")
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
        if self.grayscale and self.gray_gamma != 1.0:
            # 浅色（黄、浅蓝等）转灰度后几乎发白，压暗中间调让这些标注仍可读
            img = img.point(lambda v: round(255 * (v / 255) ** self.gray_gamma))
        if img.width > self.max_width:
            img = img.resize((self.max_width, round(img.height * self.max_width / img.width)), Image.LANCZOS)
            # 缩放后细线和小字会发虚；轻度锐化补回来，让阅读器无需再缩放
            if self.sharpen:
                img = img.filter(ImageFilter.UnsharpMask(radius=1.2, percent=70, threshold=3))
        img.save(path, "JPEG", quality=self.quality, optimize=True)

    def prune(self, keep_pmids: set[str]) -> None:
        if not self.out_dir.exists():
            return
        for d in self.out_dir.iterdir():
            if d.is_dir() and d.name not in keep_pmids:
                shutil.rmtree(d)
