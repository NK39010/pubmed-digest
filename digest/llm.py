"""Claude: pick the most interesting papers, then write a Chinese deep-dive for each."""

import json

import anthropic

from .pubmed import Paper

MODEL = "claude-opus-5"
BETAS = ["server-side-fallback-2026-07-01"]

SELECT_SCHEMA = {
    "type": "object",
    "properties": {
        "picks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "pmid": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["pmid", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["picks"],
    "additionalProperties": False,
}

ANALYSIS_SECTIONS = {
    "one_liner": "一句话看点（40 字以内，像公众号标题下的导语，抓住最反直觉或最惊艳的点）",
    "background": "研究背景：这个领域卡在哪里，为什么这个问题重要",
    "findings": "核心发现：具体做了什么、得到什么结果，尽量保留关键数字",
    "methods": "方法亮点：实验或计算设计上的巧思",
    "why_interesting": "为什么值得读：对领域或对合成生物学/蛋白工程研究者的启发",
    "caveats": "局限与疑问：证据的边界、可能的替代解释、值得追问的问题",
}

ANALYSIS_SCHEMA = {
    "type": "object",
    "properties": {"title_zh": {"type": "string"}, **{k: {"type": "string"} for k in ANALYSIS_SECTIONS}},
    "required": ["title_zh", *ANALYSIS_SECTIONS],
    "additionalProperties": False,
}


class Curator:
    def __init__(self):
        self.client = anthropic.Anthropic()

    def _json_call(self, prompt: str, schema: dict, effort: str) -> dict | None:
        with self.client.beta.messages.stream(
            model=MODEL,
            max_tokens=32000,
            betas=BETAS,
            fallbacks="default",
            thinking={"type": "adaptive"},
            output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            msg = stream.get_final_message()
        if msg.stop_reason == "refusal":
            print(f"  ! Claude 拒绝了该请求（{msg.stop_details}），跳过")
            return None
        text = next(b.text for b in msg.content if b.type == "text")
        return json.loads(text)

    def select(self, papers: list[Paper], interests: str, n_min: int, n_max: int) -> list[tuple[Paper, str]]:
        listing = "\n\n".join(
            f"<paper pmid=\"{p.pmid}\">\n期刊: {p.journal}\n标题: {p.title}\n类型: {', '.join(p.pub_types)}\n摘要: {p.abstract}\n</paper>"
            for p in papers
        )
        prompt = f"""下面是最近几天 PubMed 收录的顶刊论文候选（{len(papers)} 篇）。请为一份面向合成生物学、生物信息学和生命科学研究者的精选推送挑出 {n_min}-{n_max} 篇最"有意思"的论文。

读者画像：
{interests.strip()}

"有意思"指：结论出人意料或挑战既有认知、方法上有巧思、打开了新的研究方向、或者有很强的可迁移启发。领域影响力大但内容平淡的增量工作不算。如果候选里只有一篇真正出彩，就只选一篇。

reason 用中文写 1-2 句，说明为什么选它。pmid 必须原样来自候选列表。

{listing}"""
        result = self._json_call(prompt, SELECT_SCHEMA, effort="high")
        if not result:
            return []
        by_pmid = {p.pmid: p for p in papers}
        picks = [(by_pmid[x["pmid"]], x["reason"]) for x in result["picks"] if x["pmid"] in by_pmid]
        return picks[:n_max]

    def analyze(self, paper: Paper, reason: str) -> dict | None:
        source = "全文" if paper.full_text else "摘要（未获取到开放获取全文）"
        fields = "\n".join(f"- {k}: {v}" for k, v in ANALYSIS_SECTIONS.items())
        prompt = f"""请为下面这篇论文写一篇中文深度解读，读者是合成生物学/生信方向的研究生和科研人员，会在电子墨水阅读器上用 RSS 阅读。

你手上的材料是论文的{source}。只依据材料里的内容写作；材料没有提到的数据或细节不要编造，如果只有摘要，就在 caveats 里说明解读基于摘要、细节有待全文核实。专业术语首次出现时保留英文原词。

入选理由：{reason}

输出字段：
- title_zh: 中文标题（准确传达论文结论，不要标题党）
{fields}

除 one_liner 外，每个字段写 1-3 段，段落之间用空行分隔，不要用 Markdown 标记。

<paper>
标题: {paper.title}
期刊: {paper.journal}（{paper.pub_date}）
作者: {', '.join(paper.authors[:8])}{' 等' if len(paper.authors) > 8 else ''}
摘要:
{paper.abstract}
{f'''
全文:
{paper.full_text}''' if paper.full_text else ''}
</paper>"""
        return self._json_call(prompt, ANALYSIS_SCHEMA, effort="high")
