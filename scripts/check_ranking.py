from __future__ import annotations

import argparse
import json
import os
import re
import smtplib
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from email.message import EmailMessage
from html import unescape
from pathlib import Path
from typing import Any


TARGET_URL = "https://www.javlibrary.com/cn/star_mostfav.php"
SCRAPERAPI_URL = "https://api.scraperapi.com"
JAVLIBRARY_BASE_URL = "https://www.javlibrary.com/cn/"
STATE_PATH = Path("data/latest.json")
EXPECTED_RANKING_SIZE = 20
SMTP_HOST = "smtp.qq.com"
SMTP_PORT = 465


class RankingError(Exception):
    pass


@dataclass(frozen=True)
class EvaluationResult:
    status: str
    should_send_email: bool
    should_write_state: bool
    previous_ranking: list[dict[str, Any]]
    current_ranking: list[dict[str, Any]]


def fetch_ranking_html(api_key: str, timeout: int = 120) -> str:
    params = urllib.parse.urlencode(
        {
            "api_key": api_key,
            "url": TARGET_URL,
            "render": "true",
        }
    )
    request_url = f"{SCRAPERAPI_URL}?{params}"
    request = urllib.request.Request(
        request_url,
        headers={
            "User-Agent": "javlibrary-ranking-monitor/1.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = getattr(response, "status", None)
            body = response.read()
    except urllib.error.HTTPError as exc:
        message = exc.read().decode("utf-8", errors="replace").strip()
        raise RankingError(f"ScraperAPI 请求失败: HTTP {exc.code}. {message}") from exc
    except urllib.error.URLError as exc:
        raise RankingError(f"ScraperAPI 请求失败: {exc.reason}") from exc

    if status and status >= 400:
        raise RankingError(f"ScraperAPI 请求失败: HTTP {status}")
    return body.decode("utf-8", errors="replace")


def parse_ranking(html: str) -> list[dict[str, Any]]:
    if 'class="starbox"' not in html and "class='starbox'" not in html:
        raise RankingError("页面中未找到 starbox 排行榜容器")

    pattern = re.compile(
        r'<div\s+id=["\'](?P<id>[^"\']+)["\']\s+class=["\']searchitem["\']>\s*'
        r"<h3>(?P<rank_html>.*?)</h3>\s*"
        r'<a\s+href=["\'](?P<href>[^"\']+)["\']>(?P<body>.*?)</a>\s*</div>',
        re.IGNORECASE | re.DOTALL,
    )
    items: list[dict[str, Any]] = []
    for match in pattern.finditer(html):
        rank = parse_rank(match.group("rank_html"))
        body = remove_table(match.group("body"))
        name = normalize_text(strip_tags(body))
        if not name:
            raise RankingError(f"排行榜第 {rank} 名缺少演员姓名")
        items.append(
            {
                "rank": rank,
                "id": match.group("id"),
                "name": name,
                "url": absolute_star_url(match.group("href")),
            }
        )

    if len(items) != EXPECTED_RANKING_SIZE:
        raise RankingError(f"期望解析到 20 条排行榜数据，实际解析到 {len(items)} 条")

    ranks = [item["rank"] for item in items]
    expected_ranks = list(range(1, EXPECTED_RANKING_SIZE + 1))
    if ranks != expected_ranks:
        raise RankingError(f"排行榜名次不连续: {ranks}")
    return items


def parse_rank(rank_html: str) -> int:
    rank_text = normalize_text(strip_tags(rank_html))
    match = re.search(r"#(\d+)", rank_text)
    if not match:
        raise RankingError(f"无法解析排行榜名次: {rank_text}")
    return int(match.group(1))


def strip_tags(value: str) -> str:
    return re.sub(r"<[^>]*>", "", value)


def remove_table(value: str) -> str:
    return re.sub(r"<table\b.*?</table>", "", value, flags=re.IGNORECASE | re.DOTALL)


def normalize_text(value: str) -> str:
    return " ".join(unescape(value).split())


def absolute_star_url(href: str) -> str:
    return urllib.parse.urljoin(JAVLIBRARY_BASE_URL, href)


def load_previous_ranking(state_path: Path) -> list[dict[str, Any]] | None:
    if not state_path.exists():
        return None
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RankingError(f"状态文件不是有效 JSON: {state_path}") from exc

    ranking = data.get("ranking")
    if not isinstance(ranking, list):
        raise RankingError(f"状态文件缺少 ranking 数组: {state_path}")
    validate_state_ranking(ranking, state_path)
    return ranking


def validate_state_ranking(ranking: list[dict[str, Any]], state_path: Path) -> None:
    if len(ranking) != EXPECTED_RANKING_SIZE:
        raise RankingError(f"状态文件 ranking 应包含 20 条数据: {state_path}")
    required_keys = {"rank", "id", "name", "url"}
    for index, item in enumerate(ranking, 1):
        if not isinstance(item, dict):
            raise RankingError(f"状态文件 ranking 第 {index} 条不是对象: {state_path}")
        missing_keys = sorted(required_keys - set(item))
        if missing_keys:
            raise RankingError(f"状态文件 ranking 第 {index} 条缺少字段 {', '.join(missing_keys)}: {state_path}")


def evaluate_ranking(current_ranking: list[dict[str, Any]], state_path: Path = STATE_PATH) -> EvaluationResult:
    previous_ranking = load_previous_ranking(state_path)
    if previous_ranking is None:
        return EvaluationResult("initialized", False, True, [], current_ranking)

    if ranked_ids(previous_ranking) == ranked_ids(current_ranking):
        return EvaluationResult("unchanged", False, False, previous_ranking, current_ranking)

    return EvaluationResult("changed", True, True, previous_ranking, current_ranking)


def ranked_ids(ranking: list[dict[str, Any]]) -> list[str]:
    return [str(item.get("id", "")) for item in ranking]


def write_state(current_ranking: list[dict[str, Any]], state_path: Path = STATE_PATH) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state = {
        "source_url": TARGET_URL,
        "checked_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "ranking": current_ranking,
    }
    state_path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def build_change_summary(previous_ranking: list[dict[str, Any]], current_ranking: list[dict[str, Any]]) -> str:
    previous_by_id = {item["id"]: item for item in previous_ranking}
    current_by_id = {item["id"]: item for item in current_ranking}
    lines: list[str] = []

    entered = [item for item in current_ranking if item["id"] not in previous_by_id]
    exited = [item for item in previous_ranking if item["id"] not in current_by_id]
    moved = [
        item
        for item in current_ranking
        if item["id"] in previous_by_id and previous_by_id[item["id"]]["rank"] != item["rank"]
    ]

    if moved:
        lines.append("名次变动:")
        for item in moved:
            old_rank = previous_by_id[item["id"]]["rank"]
            lines.append(f"- {item['name']}: #{old_rank} -> #{item['rank']}")

    if entered:
        if lines:
            lines.append("")
        lines.append("新进榜:")
        for item in entered:
            lines.append(f"- {item['name']} (#{item['rank']})")

    if exited:
        if lines:
            lines.append("")
        lines.append("离榜:")
        for item in exited:
            lines.append(f"- {item['name']} (#{item['rank']})")

    return "\n".join(lines) if lines else "Top 20 演员 ID 顺序无变化。"


def build_email_body(previous_ranking: list[dict[str, Any]], current_ranking: list[dict[str, Any]]) -> str:
    summary = build_change_summary(previous_ranking, current_ranking)
    ranking_lines = [
        f"#{item['rank']} {item['name']} ({item['id']})\n{item['url']}" for item in current_ranking
    ]
    return "\n\n".join(
        [
            "JAVLibrary 演员排行榜发生变化。",
            "变动摘要:\n" + summary,
            "当前 Top 20:\n" + "\n\n".join(ranking_lines),
            f"来源: {TARGET_URL}",
        ]
    )


def send_email(sender: str, auth_code: str, body: str) -> None:
    message = EmailMessage()
    message["From"] = sender
    message["To"] = sender
    message["Subject"] = "JAVLibrary 演员排行榜发生变化"
    message.set_content(body)

    try:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=60) as smtp:
            smtp.login(sender, auth_code)
            smtp.send_message(message)
    except smtplib.SMTPException as exc:
        raise RankingError(f"QQ 邮箱 SMTP 发送失败: {exc}") from exc
    except OSError as exc:
        raise RankingError(f"QQ 邮箱 SMTP 连接失败: {exc}") from exc


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RankingError(f"缺少环境变量 {name}")
    return value


def run(state_path: Path = STATE_PATH, send_notification: bool = True) -> EvaluationResult:
    api_key = require_env("SCRAPERAPI_KEY")
    email = require_env("QQ_EMAIL")
    auth_code = require_env("QQ_SMTP_AUTH_CODE")

    html = fetch_ranking_html(api_key)
    current_ranking = parse_ranking(html)
    result = evaluate_ranking(current_ranking, state_path)

    if result.should_send_email and send_notification:
        body = build_email_body(result.previous_ranking, result.current_ranking)
        send_email(email, auth_code, body)

    if result.should_write_state:
        write_state(result.current_ranking, state_path)

    return result


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="检查 JAVLibrary 演员排行榜变化并发送 QQ 邮件通知。")
    parser.add_argument("--state-path", default=str(STATE_PATH), help="排行榜状态文件路径。")
    parser.add_argument("--no-email", action="store_true", help="检测变化但不发送邮件，用于本地调试。")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        result = run(Path(args.state_path), send_notification=not args.no_email)
    except RankingError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1
    print(f"完成: {result.status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
