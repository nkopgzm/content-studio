"""CLI regressions for concrete defects found in independent review.

These are local Python process tests; they do not claim Grok Bot execution.
"""

import unittest

import test_audit_content as harness


class IndependentReviewRegressions(unittest.TestCase):
    setUp = harness.ContentAuditCLI.setUp
    run_cli = harness.ContentAuditCLI.run_cli
    report = harness.ContentAuditCLI.report

    @staticmethod
    def other_post(post_id, body):
        post = harness.make_post(post_id, body)
        post["title"] = post_id
        post["visual"].update(
            background=f"{post_id}的场景",
            composition=f"{post_id}的构图",
            outfit=f"{post_id}的衣服",
        )
        return post

    def test_chinese_body_after_hashtag_still_participates_in_duplicate_check(self):
        body = "#创作练习今天先写一个具体场景打开空白文档时你的手停在了哪里从这个动作开始再慢慢写到想表达的观点"
        payload = harness.make_payload()
        payload["posts"][0]["body"] = body
        payload["posts"].append(self.other_post("second", body))
        report = self.report(self.run_cli(payload))
        duplicates = [item for item in report["findings"] if item["code"] == "TEXT_DUPLICATE"]
        self.assertEqual(len(duplicates), 1)
        self.assertEqual(duplicates[0]["severity"], "error")
        self.assertEqual(duplicates[0]["post_id"], "second")
        self.assertEqual(duplicates[0]["related_post_id"], "first")

    def test_screen_text_forbidden_phrase_is_checked(self):
        payload = harness.make_payload()
        payload["posts"][0]["screen_text"] = ["先留下一个细节", "百分之百保证提高写作水平"]
        report = self.report(self.run_cli(payload))
        forbidden = [item for item in report["findings"] if item["code"] == "FORBIDDEN_PHRASE"]
        self.assertEqual(len(forbidden), 1)
        self.assertEqual(forbidden[0]["severity"], "error")

    def test_screen_text_length_is_not_counted_as_body_length(self):
        payload = harness.make_payload()
        payload["profile"]["max_body_chars"] = 5
        payload["posts"][0]["body"] = "甲乙丙丁戊"
        payload["posts"][0]["screen_text"] = ["这是屏幕上的文字，并不是正文。" * 15]
        report = self.report(self.run_cli(payload))
        self.assertTrue(report["summary"]["passed"])
        self.assertFalse(any(item["code"] == "BODY_TOO_LONG" for item in report["findings"]))

    def test_empty_screen_text_array_is_accepted(self):
        payload = harness.make_payload()
        payload["posts"][0]["screen_text"] = []
        report = self.report(self.run_cli(payload))
        self.assertTrue(report["summary"]["passed"])

    def test_invalid_screen_text_values_are_rejected(self):
        for value in (None, "一条字幕", [""], ["   "], [7], ["正常", False]):
            with self.subTest(value=value):
                payload = harness.make_payload()
                payload["posts"][0]["screen_text"] = value
                result = self.run_cli(payload)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)

    def test_short_text_warning_once_per_current_post_across_multiple_comparisons(self):
        payload = harness.make_payload()
        payload["posts"][0]["body"] = "留下一个动作"
        payload["posts"].extend([
            self.other_post("second", "记住一种声音"),
            self.other_post("third", "看看手边的小物件"),
        ])
        payload["history"] = [self.other_post("history", "过去的一段记录")]
        report = self.report(self.run_cli(payload))
        short_findings = [item for item in report["findings"] if item["code"] == "TEXT_TOO_SHORT"]
        self.assertEqual(len(short_findings), 3)
        self.assertEqual(sorted(item["post_id"] for item in short_findings), ["first", "second", "third"])
        self.assertTrue(all(item["severity"] == "warning" for item in short_findings))
        self.assertTrue(report["summary"]["passed"])

    def test_single_short_post_with_history_discloses_skipped_comparison(self):
        payload = harness.make_payload()
        payload["posts"][0]["body"] = "记住一束光"
        payload["history"] = [self.other_post("history", "记住一束光")]
        report = self.report(self.run_cli(payload))
        short_findings = [item for item in report["findings"] if item["code"] == "TEXT_TOO_SHORT"]
        self.assertEqual(len(short_findings), 1)
        self.assertEqual(short_findings[0]["post_id"], "first")

    def test_single_short_post_without_comparison_does_not_warn(self):
        payload = harness.make_payload()
        payload["posts"][0]["body"] = "记住一束光"
        report = self.report(self.run_cli(payload))
        self.assertFalse(any(item["code"] == "TEXT_TOO_SHORT" for item in report["findings"]))

    def test_short_text_threshold_uses_normalized_length(self):
        payload = harness.make_payload()
        payload["posts"][0]["body"] = "甲，乙。丙！丁？戊：己；庚、辛，壬。癸！子？丑：寅；卯、辰。"
        payload["posts"].append(self.other_post("second", "这是另一篇内容较长的独立草稿，只是为了提供比较对象，不使用相同内容，也不声称短文已经过完整的重复检查。"))
        report = self.report(self.run_cli(payload))
        short_findings = [item for item in report["findings"] if item["code"] == "TEXT_TOO_SHORT"]
        self.assertEqual(len(short_findings), 1)
        self.assertEqual(short_findings[0]["post_id"], "first")

    def test_sixteen_normalized_characters_do_not_get_too_short_warning(self):
        payload = harness.make_payload()
        payload["posts"][0]["body"] = "甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳"
        payload["posts"].append(self.other_post("second", "这是另一篇内容较长的独立草稿，只是为了提供比较对象，不使用相同内容，也不声称短文已经过完整的重复检查。"))
        report = self.report(self.run_cli(payload))
        self.assertFalse(any(item["code"] == "TEXT_TOO_SHORT" for item in report["findings"]))

    def test_blank_post_identity_is_unchecked_warning(self):
        for value in ("", "   "):
            with self.subTest(value=value):
                payload = harness.make_payload()
                payload["posts"][0]["visual"]["identity"]["hair_color"] = value
                report = self.report(self.run_cli(payload))
                self.assertTrue(report["summary"]["passed"])
                self.assertTrue(any(item["code"] == "IDENTITY_UNCHECKED" and item["severity"] == "warning" for item in report["findings"]))
                self.assertFalse(any(item["code"] == "IDENTITY_MISMATCH" for item in report["findings"]))

    def test_null_post_identity_is_still_input_error(self):
        payload = harness.make_payload()
        payload["posts"][0]["visual"]["identity"]["hair_color"] = None
        result = self.run_cli(payload)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)

    def test_profile_identity_anchors_stay_strict(self):
        for value in ("", "   ", None):
            with self.subTest(value=value):
                payload = harness.make_payload()
                payload["profile"]["identity_anchors"]["hair_color"] = value
                result = self.run_cli(payload)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)

    def test_hash_and_space_only_tags_are_rejected(self):
        for target in ("required", "actual"):
            with self.subTest(target=target):
                payload = harness.make_payload()
                if target == "required":
                    payload["profile"]["required_tags"] = ["# #"]
                else:
                    payload["posts"][0]["tags"] = ["# #"]
                result = self.run_cli(payload)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)

    def test_surrounding_hash_characters_are_normalized_for_tag_matching(self):
        payload = harness.make_payload()
        payload["profile"]["required_tags"] = ["#创作练习"]
        payload["posts"][0]["tags"] = ["#创作练习#"]
        report = self.report(self.run_cli(payload))
        self.assertTrue(report["summary"]["passed"])
        self.assertFalse(any(item["code"] == "MISSING_REQUIRED_TAG" for item in report["findings"]))

    def test_normalized_forbidden_phrases_are_reported_once(self):
        payload = harness.make_payload()
        payload["profile"]["forbidden_phrases"] = ["ＡＢＣ", "ABC", "abc"]
        payload["posts"][0]["body"] = "正文里包含 ABC，用于验证同一禁用词的多种书写形式只报告一次。"
        report = self.report(self.run_cli(payload))
        forbidden = [item for item in report["findings"] if item["code"] == "FORBIDDEN_PHRASE"]
        self.assertEqual(len(forbidden), 1)
        self.assertIn("ＡＢＣ", forbidden[0]["message"])

    def test_forbidden_phrases_do_not_join_across_content_fields(self):
        cases = [
            ("title and body", {"title": "guaranteed", "body": "growth"}, 0),
            ("body and tag", {"body": "guaranteed", "tags": ["growth"]}, 0),
            ("two tags", {"tags": ["guaranteed", "growth"]}, 0),
            ("two screen entries", {"screen_text": ["guaranteed", "growth"]}, 0),
            ("same field still checked", {"body": "guaranteed growth"}, 1),
            ("repeated across fields reported once", {
                "title": "guaranteed growth", "body": "guaranteed growth",
                "screen_text": ["guaranteed growth"],
            }, 1),
        ]
        for label, fields, expected_findings in cases:
            with self.subTest(case=label):
                payload = harness.make_payload()
                payload["profile"]["required_tags"] = []
                payload["profile"]["forbidden_phrases"] = ["guaranteed growth"]
                payload["posts"][0].update(fields)
                report = self.report(self.run_cli(payload))
                forbidden = [item for item in report["findings"] if item["code"] == "FORBIDDEN_PHRASE"]
                self.assertEqual(len(forbidden), expected_findings)

    def test_empty_source_object_keys_are_input_errors(self):
        for key in ("", "   "):
            with self.subTest(key=key):
                payload = harness.make_payload()
                payload["posts"][0]["sources"] = [{key: "https://example.test/source"}]
                result = self.run_cli(payload)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
