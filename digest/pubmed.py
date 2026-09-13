"""NCBI E-utilities: search recent top-journal papers and fetch their records."""

import os
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

import httpx

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
EXCLUDED_TYPES = ["Editorial", "News", "Comment", "Published Erratum", "Retraction of Publication", "Letter"]


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

    def fetch_full_text(self, pmcid: str) -> str:
        """Body text from PMC (open-access articles only); empty string if unavailable."""
        try:
            resp = self._get("efetch.fcgi", db="pmc", id=pmcid.removeprefix("PMC"), retmode="xml")
            root = ET.fromstring(resp.content)
        except (httpx.HTTPError, ET.ParseError):
            return ""
        body = root.find(".//body")
        if body is None:
            return ""
        sections = []
        for sec in body.iter("sec"):
            heading = sec.findtext("title") or ""
            paras = ["".join(p.itertext()).strip() for p in sec.findall("p")]
            if paras:
                sections.append(f"## {heading}\n" + "\n".join(paras))
        return "\n\n".join(sections)


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
