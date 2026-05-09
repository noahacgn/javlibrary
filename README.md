# JAVLibrary 演员排行榜邮件通知

这个仓库每天检查 JAVLibrary 演员排行榜：

https://www.javlibrary.com/cn/star_mostfav.php

脚本通过 ScraperAPI 抓取页面，只比较 Top 20 演员的 ID 顺序。演员顺序变化时，会用 QQ 邮箱给自己发送邮件；没有变化时不发邮件，也不提交新的状态文件。

## GitHub Secrets

在 GitHub 仓库的 `Settings -> Secrets and variables -> Actions` 里配置：

| Secret | 说明 |
| --- | --- |
| `SCRAPERAPI_KEY` | ScraperAPI API key |
| `QQ_EMAIL` | QQ 邮箱地址，例如 `123456@qq.com` |
| `QQ_SMTP_AUTH_CODE` | QQ 邮箱 SMTP 授权码，不是网页登录密码 |

QQ 邮箱需要先在网页端开启 SMTP/IMAP 服务，并生成授权码。脚本使用 `smtp.qq.com:465` 和 SSL 登录，同一个 `QQ_EMAIL` 自发自收。

## 运行方式

GitHub Actions 会在每天北京时间 09:00 自动运行，也可以在 Actions 页面手动触发 `Daily JAVLibrary ranking check`。

首次运行没有 `data/latest.json` 时，只会初始化基线状态文件，不会发送邮件。之后只有 Top 20 演员 ID 顺序变化时才发送邮件并更新状态文件。

## 本地验证

运行单元测试：

```bash
python -m unittest discover -s tests
```

本地真实抓取验证需要设置环境变量：

```bash
python scripts/check_ranking.py --no-email --state-path data/local-test.json
```

`--no-email` 只跳过邮件发送；仍然会检查环境变量和抓取解析。不要把真实密钥写进仓库文件。

## 设计说明

- 不使用第三方 Python 依赖，网络请求、HTML 解析、JSON 和邮件发送都使用标准库。
- ScraperAPI 固定使用 `render=true`，这是前期验证能绕过 Cloudflare 并拿到排行榜数据的参数。
- 页面上的 `▲`、`▼` 标记和头像链接变化不会触发邮件；只有 Top 20 演员 ID 顺序变化才触发。
- 公开仓库如果长期没有活动，GitHub 可能停用定时 workflow；当前方案不做无变化心跳提交，保持提交历史干净。
