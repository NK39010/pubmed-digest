"""NCBI E-utilities: search recent top-journal papers and fetch their records."""

import os
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

import httpx

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
EXCLUDED_TYPES = ["Editorial", "News", "Comment", "Published Erratum", "Retraction of Publication", "Letter"]


XLINK_HREF = "{http://www.w3.org/1999/xlink}href"


@dataclass
class Figure:
    id: str          # e.g. "F2"
    label: str       # e.g. "Figure 2."
    caption: str     # original English legend
    href: str        # graphic file name in the PMC package, e.g. "gkag877fig2.webp"
    source_key: str = ""  # object key in the PMC OA S3 bucket
    image_url: str = ""   # filled after download, absolute URL on our site


@dataclass
class Paper:
    pmid: str
    title: str
    abstract: str
    journal: str
    pub_date: str
    authors: list[str] = field(default_factory=list)
    doi: str = ""
    pmcid: str = ""
    pub_types: list[str] = field(default_factory=list)
    full_text: str = ""
    figures: list[Figure] = field(default_factory=list)
    license: str = ""
    open_access: bool = False  # present in the PMC Open Access dataset (full text + figures downloadable)

    @property
    def url(self) -> str:
        return f"https://doi.org/{self.doi}" if self.doi else f"https://pubmed.ncbi.nlm.nih.gov/{self.pmid}/"


class PubMed:
    def __init__(self, tool: str, email: str):
        self.base_params = {"tool": tool}
        if email:
            self.base_params["email"] = email
        if key := os.environ.get("NCBI_API_KEY"):
            self.base_params["api_key"] = key
        self.delay = 0.11 if "api_key" in self.base_params else 0.35
        self.http = httpx.Client(timeout=60, follow_redirects=True)

    def _get(self, endpoint: str, **params) -> httpx.Response:
        for attempt in range(4):
            time.sleep(self.delay)
            resp = self.http.get(f"{EUTILS}/{endpoint}", params={**self.base_params, **params})
            if resp.status_code == 429 or resp.status_code >= 500:
                time.sleep(2 ** attempt)
                continue
            resp.raise_for_status()
            return resp
        resp.raise_for_status()
        return resp

    @staticmethod
    def build_query(cfg: dict) -> str:
        journals = lambda names: " OR ".join(f'"{j}"[ta]' for j in names)
        topics = " OR ".join(f'"{t}"[tiab]' for t in cfg["topic_terms"])
        excluded = " OR ".join(f'"{t}"[pt]' for t in EXCLUDED_TYPES)
        return (
            f"(({journals(cfg['specialist_journals'])}) OR "
            f"(({journals(cfg['general_journals'])}) AND ({topics}))) "
            f"AND hasabstract NOT ({excluded}) NOT review[pt]"
        )

    def search(self, query: str, days: int, retmax: int) -> list[str]:
        resp = self._get(
            "esearch.fcgi", db="pubmed", term=query, retmode="json",
            datetype="edat", reldate=days, retmax=retmax, sort="pub_date",
        )
        return resp.json()["esearchresult"]["idlist"]

    def fetch(self, pmids: list[str]) -> list[Paper]:
        papers = []
        for i in range(0, len(pmids), 100):
            batch = pmids[i:i + 100]
            resp = self.http.post(
                f"{EUTILS}/efetch.fcgi",
                data={**self.base_params, "db": "pubmed", "id": ",".join(batch), "retmode": "xml"},
            )
            resp.raise_for_status()
            time.sleep(self.delay)
            root = ET.fromstring(resp.content)
            papers.extend(_parse_article(a) for a in root.iter("PubmedArticle"))
        return papers

    def link_pmc(self, papers: list[Paper]) -> None:
        """Fill in missing PMCIDs by DOI. Fresh PubMed records often aren't linked to PMC yet (elink returns nothing)."""
        by_doi = {p.doi.lower(): p for p in papers if not p.pmcid and p.doi}
        dois = list(by_doi)
        for i in range(0, len(dois), 50):
            batch = dois[i:i + 50]
            try:
                term = " OR ".join(f'"{d}"[doi]' for d in batch)
                ids = self._get("esearch.fcgi", db="pmc", term=term, retmode="json", retmax=len(batch) * 2).json()["esearchresult"]["idlist"]
                if not ids:
                    continue
                result = self._get("esummary.fcgi", db="pmc", id=",".join(ids), retmode="json").json()["result"]
            except (httpx.HTTPError, KeyError, ValueError):
                continue
            for uid in result.get("uids", []):
                article_ids = {a["idtype"]: a["value"] for a in result[uid].get("articleids", [])}
                # [doi] is translated to [All Fields] by PMC search, so confirm the DOI really matches
                if (paper := by_doi.get(article_ids.get("doi", "").lower())) and article_ids.get("pmcid"):
                    paper.pmcid = article_ids["pmcid"]

    def fetch_pmc(self, paper: Paper) -> None:
        """Body text and figure legends from PMC (open-access articles only); leaves fields empty if unavailable."""
        try:
            resp = self._get("efetch.fcgi", db="pmc", id=paper.pmcid.removeprefix("PMC"), retmode="xml")
            root = ET.fromstring(resp.content)
        except (httpx.HTTPError, ET.ParseError):
            return
        body = root.find(".//body")
        if body is None:
            return
        sections = []
        for sec in body.iter("sec"):
            heading = sec.findtext("title") or ""
            paras = ["".join(p.itertext()).strip() for p in sec.findall("p")]
            if paras:
                sections.append(f"## {heading}\n" + "\n".join(paras))
        paper.full_text = "\n\n".join(sections)

        supplementary = {id(f) for sm in root.iter("supplementary-material") for f in sm.iter("fig")}
        for fig in root.iter("fig"):
            graphic = fig.find(".//graphic")
            if id(fig) in supplementary or graphic is None or not fig.get("id"):
                continue
            paper.figures.append(Figure(
                id=fig.get("id"),
                label=_text(fig.find("label")),
                caption=" ".join(_text(fig.find("caption")).split()),
                href=graphic.get(XLINK_HREF, ""),
            ))


def _text(el) -> str:
    return "".join(el.itertext()).strip() if el is not None else ""


def _parse_article(article: ET.Element) -> Paper:
    cit = article.find("MedlineCitation")
    art = cit.find("Article")
    abstract_parts = []
    for node in art.findall("Abstract/AbstractText"):
        label = node.get("Label")
        abstract_parts.append(f"{label}: {_text(node)}" if label else _text(node))

    authors = []
    for au in art.findall("AuthorList/Author"):
        name = " ".join(filter(None, [au.findtext("ForeName"), au.findtext("LastName")])) or au.findtext("CollectiveName")
        if name:
            authors.append(name)

    date = art.find("Journal/JournalIssue/PubDate")
    pub_date = " ".join(filter(None, [date.findtext("Year"), date.findtext("Month"), date.findtext("Day")])) if date is not None else ""
    if not pub_date and date is not None:
        pub_date = date.findtext("MedlineDate") or ""

    ids = {i.get("IdType"): (i.text or "") for i in article.findall("PubmedData/ArticleIdList/ArticleId")}
    return Paper(
        pmid=cit.findtext("PMID"),
        title=_text(art.find("ArticleTitle")),
        abstract="\n".join(abstract_parts),
        journal=art.findtext("Journal/Title") or "",
        pub_date=pub_date,
        authors=authors,
        doi=ids.get("doi", ""),
        pmcid=ids.get("pmc", ""),
        pub_types=[_text(pt) for pt in art.findall("PublicationTypeList/PublicationType")],
    )
