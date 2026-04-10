# GitHub Actions 上线清单（A股每日基本面推送到飞书）

## 1. 提交这 3 个文件到你的仓库
- `.github/workflows/daily_analysis.yml`（已调优为 `REPORT_TYPE=full`）
- `.github/actions/a_share_daily_variables.example`
- `.github/actions/a_share_daily_secrets.example`

## 2. 在 GitHub 配置 Secrets
路径：`Repository -> Settings -> Secrets and variables -> Actions -> Secrets`

按 `.github/actions/a_share_daily_secrets.example` 新建：
- 必填：`FEISHU_WEBHOOK_URL`
- 必填（至少一个）：`GEMINI_API_KEY` 或 `OPENAI_API_KEY`
- 建议：`TUSHARE_TOKEN`
- 可选：`FEISHU_WEBHOOK_SECRET`、`FEISHU_WEBHOOK_KEYWORD`

注意：
- `FEISHU_APP_ID` / `FEISHU_APP_SECRET` 不是群 Webhook 推送必需项，不能替代 `FEISHU_WEBHOOK_URL`。

## 3. 在 GitHub 配置 Variables
路径：`Repository -> Settings -> Secrets and variables -> Actions -> Variables`

按 `.github/actions/a_share_daily_variables.example` 新建，最少确保：
- `STOCK_LIST`
- `REPORT_TYPE=full`
- `ENABLE_FUNDAMENTAL_PIPELINE=true`
- `MARKET_REVIEW_REGION=cn`

## 4. 先手动跑一次验证
路径：`Actions -> 每日股票分析 -> Run workflow`

建议参数：
- `mode=full`
- `force_run=true`（首次联调用，避免因交易日判断被跳过）

## 5. 验收标准
- 飞书群收到完整日报
- 日报含基本面块（财报摘要/分红/资金流/板块）
- Actions 成功，Artifacts 中有 `reports/` 和 `logs/`

## 6. 定时说明
- 当前 cron：`0 10 * * 1-5`
- 即：每个工作日 北京时间 18:00 自动执行

