# pubmed-digest

每天从 PubMed 顶刊里挑 1-2 篇合成生物学/生信/生命科学论文，用 DeepSeek 写中文深度解读，输出成 RSS，墨水屏阅读器订阅。

```
PubMed E-utilities（近 N 天，顶刊 + 主题词）
  → deepseek-v4-pro 从候选里挑 1-2 篇"有意思"的
  → 有 PMC 开放全文就读全文，否则读摘要
  → 写结构化中文解读（看点 / 背景 / 发现 / 方法 / 启发 / 局限）
  → docs/feed.xml（RSS 2.0，全文输出）
```

## 本地运行

```powershell
.venv\Scripts\python.exe run.py --dry-run   # 只看候选，不花钱
$env:DEEPSEEK_API_KEY = "sk-..."
.venv\Scripts\python.exe run.py             # 完整运行，生成 docs/feed.xml
```

期刊、主题词、兴趣画像、每次篇数、模型都在 `config.toml` 里改。换通义千问/Kimi/智谱等 OpenAI 兼容接口，改 `[llm]` 的三项即可。

## 网页端：阅读、排版、导出公众号

- 在线查看：`https://<用户名>.github.io/pubmed-digest/`，可以排版和复制到公众号，改动只存在当前浏览器
- 本地编辑：`.venv\Scripts\python.exe serve.py`，会多出"保存到仓库"按钮，写回 `data/items.json` 并重新生成 `feed.xml`，之后 `git push` 发布

页面功能：三套模板（简洁/学术/杂志）+ 字号、行距、主色调整；直接在正文上编辑；"公众号效果"按手机宽度预览；"复制到公众号"把全部样式内联后放进剪贴板，到公众号后台编辑器粘贴即可。

公众号限制：外链会被转成纯文本；**插图需要在公众号编辑器里手动上传**（页面底部列出了本篇所有图片链接），因为微信不一定能抓取 github.io 上的图片。

## 部署（GitHub Actions + Pages）

1. 仓库 Settings → Secrets and variables → Actions，添加 `DEEPSEEK_API_KEY`（可选 `NCBI_API_KEY`）
2. Settings → Pages → Source 选 `Deploy from a branch`，分支 `main`，目录 `/docs`
3. Actions 页面手动触发一次 `daily-digest`，之后每天北京时间 08:30 自动运行
4. 阅读器订阅 `https://<用户名>.github.io/pubmed-digest/feed.xml`

## 成本

每次约 55-120 篇摘要做筛选 + 1-2 篇解读，deepseek-v4-pro 下一次约几毛钱人民币（有全文时偏高）。
