# PubMed 文献自动监控 - GitHub Actions 部署指南

## 功能
- 每天自动检索 PubMed 文献
- 标题自动中文翻译
- 按影响因子排序，过滤 IF<=1
- 分类推送到邮箱

## 部署步骤

### 1. 创建 GitHub 账号
访问 https://github.com 注册（已有跳过）

### 2. 创建新仓库
1. 点击右上角 "+" → "New repository"
2. 仓库名：`pubmed-monitor`
3. 选择 "Public"（免费）
4. 勾选 "Add a README file"
5. 点击 "Create repository"

### 3. 上传文件
1. 进入仓库，点击 "Add file" → "Upload files"
2. 拖拽上传 `pubmed_monitor_github` 文件夹中的所有文件：
   - `pubmed_monitor.py`
   - `config.json`
   - `.github/workflows/pubmed.yml`
3. 点击 "Commit changes"

### 4. 设置 Secrets（重要！）
1. 进入仓库 → "Settings" → "Secrets and variables" → "Actions"
2. 点击 "New repository secret"，依次添加：

| Name | Value |
|------|-------|
| `SENDER_EMAIL` | `13908691257@163.com` |
| `SENDER_PASSWORD` | `XMuGZgf3z8aNPxGi` |
| `RECEIVER_EMAIL` | `3094249436@qq.com` |

### 5. 启用 Actions
1. 进入仓库 → "Actions" 标签
2. 点击 "I understand my workflows, go ahead and enable them"

### 6. 测试运行
1. "Actions" → "PubMed 文献监控" → "Run workflow"

## 自动执行时间
- 默认：每天北京时间 **16:00**（UTC 08:00）
- 修改：编辑 `.github/workflows/pubmed.yml` 中的 `cron: '0 8 * * *'`

## 修改关键词
编辑 `config.json` 中的 `keywords` 数组

## 查看日志
"Actions" → 点击最近的运行 → 查看详情

## 注意事项
- 免费账户每月 2000 分钟运行时间，足够每天运行
- 仓库必须是 Public 才能免费使用 Actions
- Secrets 不会泄露，代码中看不到密码
