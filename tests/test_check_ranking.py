import json
import tempfile
import unittest
from pathlib import Path

from scripts import check_ranking


def ranking_item(rank, star_id, name):
    return {
        "rank": rank,
        "id": star_id,
        "name": name,
        "url": f"https://www.javlibrary.com/cn/vl_star.php?s={star_id}",
    }


def ranking(ids=None):
    ids = ids or [f"aa{i:03d}" for i in range(1, 21)]
    names_by_id = {
        "aa001": "小松空",
        "aa002": "早坂奏音",
        "aa003": "いち花",
        "new01": "新人",
    }
    return [ranking_item(index, star_id, actor_name(star_id, names_by_id)) for index, star_id in enumerate(ids, 1)]


def actor_name(star_id, names_by_id):
    if star_id in names_by_id:
        return names_by_id[star_id]
    return f"演员{int(star_id[-3:])}"


def html_for(ids=None):
    items = []
    for item in ranking(ids):
        href = f"vl_star.php?s={item['id']}"
        items.append(
            f'<div id="{item["id"]}" class="searchitem">'
            f'<h3>#{item["rank"]} <span style="color:#00ff00;">▲</span></h3>'
            f'<a href="{href}"><table class="portrait"><tbody><tr><td>'
            f'<img src="../img/icn-portrait.jpg" title="https://example.test/{item["id"]}.jpg">'
            f'</td></tr></tbody></table>{item["name"]}</a></div>'
        )
    return '<html><body><div class="starbox">' + "".join(items) + "</div></body></html>"


class ParseRankingTests(unittest.TestCase):
    def test_parses_top_20_from_minimal_html(self):
        items = check_ranking.parse_ranking(html_for())

        self.assertEqual(20, len(items))
        self.assertEqual(ranking_item(1, "aa001", "小松空"), items[0])
        self.assertEqual(ranking_item(2, "aa002", "早坂奏音"), items[1])
        self.assertEqual(ranking_item(3, "aa003", "いち花"), items[2])

    def test_rejects_html_without_starbox(self):
        with self.assertRaisesRegex(check_ranking.RankingError, "starbox"):
            check_ranking.parse_ranking("<html></html>")

    def test_rejects_incomplete_ranking(self):
        with self.assertRaisesRegex(check_ranking.RankingError, "20"):
            check_ranking.parse_ranking(html_for(["aa001", "aa002"]))


class RankingChangeTests(unittest.TestCase):
    def test_missing_previous_state_initializes_without_email(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "latest.json"
            result = check_ranking.evaluate_ranking(ranking(), state_path)

            self.assertEqual("initialized", result.status)
            self.assertFalse(result.should_send_email)
            self.assertTrue(result.should_write_state)

    def test_same_ranked_ids_do_not_notify_or_write_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "latest.json"
            state_path.write_text(
                json.dumps({"source_url": check_ranking.TARGET_URL, "checked_at": "old", "ranking": ranking()}),
                encoding="utf-8",
            )

            result = check_ranking.evaluate_ranking(ranking(), state_path)

            self.assertEqual("unchanged", result.status)
            self.assertFalse(result.should_send_email)
            self.assertFalse(result.should_write_state)

    def test_malformed_state_fails_fast(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "latest.json"
            state_path.write_text(
                json.dumps({"source_url": check_ranking.TARGET_URL, "checked_at": "old", "ranking": [{"rank": 1}]}),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(check_ranking.RankingError, "状态文件"):
                check_ranking.evaluate_ranking(ranking(), state_path)

    def test_rank_changes_are_summarized(self):
        old = ranking(["aa001", "aa002"] + [f"aa{i:03d}" for i in range(3, 21)])
        new = ranking(["aa002", "aa001"] + [f"aa{i:03d}" for i in range(3, 21)])

        summary = check_ranking.build_change_summary(old, new)

        self.assertIn("早坂奏音: #2 -> #1", summary)
        self.assertIn("小松空: #1 -> #2", summary)

    def test_entries_and_exits_are_summarized(self):
        old = ranking([f"aa{i:03d}" for i in range(1, 21)])
        new_ids = [f"aa{i:03d}" for i in range(1, 20)] + ["new01"]
        new = ranking(new_ids)

        summary = check_ranking.build_change_summary(old, new)

        self.assertIn("新进榜", summary)
        self.assertIn("新人 (#20)", summary)
        self.assertIn("离榜", summary)
        self.assertIn("演员20 (#20)", summary)

    def test_email_body_contains_summary_and_current_top_20(self):
        old = ranking([f"aa{i:03d}" for i in range(1, 21)])
        new = ranking(["aa002", "aa001"] + [f"aa{i:03d}" for i in range(3, 21)])

        body = check_ranking.build_email_body(old, new)

        self.assertIn("变动摘要", body)
        self.assertIn("当前 Top 20", body)
        self.assertIn("#1 早坂奏音", body)
        self.assertIn("https://www.javlibrary.com/cn/vl_star.php?s=aa002", body)


if __name__ == "__main__":
    unittest.main()
