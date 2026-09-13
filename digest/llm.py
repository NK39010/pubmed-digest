"""LLM (any OpenAI-compatible API, default DeepSeek): pick interesting papers, then write a Chinese deep-dive."""

import json
import os

from openai import OpenAI

from .pubmed import Paper

ANALYSIS_SECTIONS = {
    "one_liner": "一句话看点（40 字以内，像公众号标题下的导语，抓住最反直觉或最惊艳的点）",
    "background": "研究背景：这个领域卡在哪里，为什么这个问题重要",
    "findings": "核心发现：具体做了什么、得到什么结果，尽量保留关键数字",
    "methods": "方法亮点：实验或计算设计上的巧思",
    "why_interesting": "为什么值得读：对领域或对合成生物学/蛋白工程研究者的启发",
    "caveats": "局限与疑问：证据的边界、可能的替代解释、值得追问的问题",
}


class Curator:
    def __init__(self, cfg: dict):
        key = os.environ.get(cfg["api_key_env"])
        if not key:
            raise SystemExit(f"缺少环境变量 {cfg['api_key_env']}")
        self.client = OpenAI(api_key=key, base_url=cfg["base_url"], timeout=600)
        self.model = cfg["model"]

    def _json_call(self, prompt: str, required: list[str]) -> dict | None:
        for attempt in range(3):
            resp = self.client.chat.completions.create(
                model=self.model,
                max_tokens=32000,
                response_format={"type": "json_object"},
                messages=[{"role": "user", "content": prompt}],
            )
            try:
                data = json.loads(resp.choices[0].message.content or "")
            except json.JSONDecodeError:
                data = None
            if isinstance(data, dict) and all(k in data for k in required):
                return data
            print(f"  ! 第 {attempt + 1} 次返回的 JSON 不完整，重试")
        return None

    def select(self, papers: list[Paper], interests: str, n_min: int, n_max: int, prefer_oa: bool) -> list[tuple[Paper, str]]:
        listing = "\n\n".join(
            f"<paper pmid=\"{p.pmid}\">\n期刊: {p.journal}\n标题: {p.title}\n类型: {', '.join(p.pub_types)}\n"
            f"开放全文与原图: {'有' if p.open_access else '无'}\n摘要: {p.abstract}\n</paper>"
            for p in papers
        )
        oa_hint = (
            "\n读者希望解读里附原文插图，只有标注\"开放全文与原图: 有\"的论文能做到。两篇论文同样有意思时优先选有开放全文的；"
            "但如果最出彩的论文没有开放全文，仍然选它。\n"
            if prefer_oa else ""
        )
        prompt = f"""下面是最近几天 PubMed 收录的顶刊论文候选（{len(papers)} 篇）。请为一份面向合成生物学、生物信息学和生命科学研究者的精选推送挑出 {n_min}-{n_max} 篇最"有意思"的论文。

读者画像：
{interests.strip()}

"有意思"指：结论出人意料或挑战既有认知、方法上有巧思、打开了新的研究方向、或者有很强的可迁移启发。领域影响力大但内容平淡的增量工作不算。如果候选里只有一篇真正出彩，就只选一篇。
{oa_hint}
以 JSON 输出，格式示例：
{{"picks": [{{"pmid": "12345678", "reason": "用中文写 1-2 句入选理由"}}]}}
pmid 必须原样来自候选列表。

{listing}"""
        result = self._json_call(prompt, ["picks"])
        if not result:
            return []
        by_pmid = {p.pmid: p for p in papers}
        picks = [(by_pmid[str(x.get("pmid"))], x.get("reason", "")) for x in result["picks"] if str(x.get("pmid")) in by_pmid]
        return picks[:n_max]

    def analyze(self, paper: Paper, reason: str, max_figures: int) -> dict | None:
        source = "全文" if paper.full_text else "摘要（未获取到开放获取全文）"
        fields = ",\n".join(f'  "{k}": "{v}"' for k, v in ANALYSIS_SECTIONS.items())
        figure_task = figure_block = ""
        if paper.figures:
            placeable = " / ".join(k for k in ANALYSIS_SECTIONS if k != "one_liner")
            figure_task = f"""  "figures": [
    {{
      "id": "图的 id，原样取自 <figures> 里的 id 属性",
      "title_zh": "这张图在说明什么（15 字以内的中文小标题）",
      "caption_zh": "图例的完整中文翻译：逐句翻译原图例，保留 (A)(B) 等分图编号、统计方法和样本量，不删减",
      "explanation": "图解：先说这张图要回答什么问题，再按分图说明怎么读、看到了什么、支持了哪个结论。1-2 段",
      "place_after": "插在哪个字段的正文之后，取值：{placeable}"
    }}
  ],
"""
            figure_block = "\n<figures>\n" + "\n".join(
                f'<figure id="{f.id}" label="{f.label}">{f.caption}</figure>' for f in paper.figures
            ) + "\n</figures>"
            figure_rule = (
                f"\n论文附有 {len(paper.figures)} 张图（图例见 <figures>）。从中挑出最能支撑核心结论的 1-{max_figures} 张放进 figures，"
                "按论文中的顺序排列；只挑承载关键证据或关键设计的图，示意性的图形摘要除非信息量大否则不选。"
                "正文里提到这些图时用\"（见图 N）\"标注，N 用原文图号。"
            )
        else:
            figure_rule = ""
        prompt = f"""请为下面这篇论文写一篇中文深度解读，读者是合成生物学/生信方向的研究生和科研人员，会在墨水屏阅读器上用 RSS 阅读。

你手上的材料是论文的{source}。只依据材料里的内容写作；材料没有提到的数据或细节不要编造，如果只有摘要，就在 caveats 里说明解读基于摘要、细节有待全文核实。专业术语首次出现时保留英文原词。{figure_rule}

入选理由：{reason}

以 JSON 输出，包含以下字段（值替换为你写的内容）：
{{
{figure_task}  "title_zh": "中文标题（准确传达论文结论，不要标题党）",
{fields}
}}
background、findings、methods、why_interesting、caveats 每个写 1-3 段，段落之间用 \\n\\n 分隔，不要用 Markdown 标记。

<paper>
标题: {paper.title}
期刊: {paper.journal}（{paper.pub_date}）
作者: {', '.join(paper.authors[:8])}{' 等' if len(paper.authors) > 8 else ''}
摘要:
{paper.abstract}
{f'''
全文:
{paper.full_text}''' if paper.full_text else ''}{figure_block}
</paper>"""
        required = ["title_zh", *ANALYSIS_SECTIONS] + (["figures"] if paper.figures else [])
        return self._json_call(prompt, required)
