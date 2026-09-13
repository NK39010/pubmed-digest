# pubmed-digest

每天从 PubMed 顶刊里挑 1-2 篇合成生物学/生信/生命科学论文，用 Claude 写中文深度解读，输出成 RSS，在墨水瓶里订阅。

```
PubMed E-utilities（近 N 天，顶刊 + 主题词）
  → Claude Opus 5 从候选里挑 1-2 篇"有意思"的
  → 有 PMC 开放全文就读全文，否则读摘要
  → Claude 写结构化中文解读（看点 / 背景 / 发现 / 方法 / 启发 / 局限）
  → docs/feed.xml（RSS 2.0，全文输出）
```

## 本地运行

```powershell
.venv\Scripts\python.exe run.py --dry-run   # 只看候选，不花钱
$env:ANTHROPIC_API_KEY = "sk-ant-..."
.venv\Scripts\python.exe run.py             # 完整运行，生成 docs/feed.xml
```

期刊、主题词、兴趣画像、每次篇数都在 `config.toml` 里改。

## 部署（GitHub Actions + Pages，免费、无需服务器）

1. 在 GitHub 新建仓库 `pubmed-digest`，把本目录推上去
2. 仓库 Settings → Secrets and variables → Actions，添加 `ANTHROPIC_API_KEY`（可选 `NCBI_API_KEY`）
3. Settings → Pages → Source 选 `Deploy from a branch`，分支 `main`，目录 `/docs`
4. 把 `config.toml` 里的 `site_url` 改成 `https://<用户名>.github.io/pubmed-digest/`
5. Actions 页面手动触发一次 `daily-digest`，之后每天北京时间 08:30 自动运行
6. 墨水瓶里订阅 `https://<用户名>.github.io/pubmed-digest/feed.xml`

## 成本

每次约 55-120 篇摘要做筛选 + 1-2 篇解读，Opus 5 下一次大约 $0.5-1.5（有全文时偏高）。
