"""Independent black-box acceptance tests for the offline content audit CLI.

Run from the plugin root with ``python -m unittest discover -s tests -v``.
Only the Python standard library is needed. Tests create synthetic content and
temporary files; they do not use real account data or call a model/service.
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PLUGIN_ROOT / "skills" / "content-studio" / "scripts" / "audit_content.py"


def make_post(post_id="first", body="今天把桌面整理成适合阅读的小角落。先收起闲置物品，再留下一盏灯和一本书。"):
    return {
        "id": post_id,
        "title": "给阅读留一点空间",
        "body": body,
        "tags": ["#阅读", "#生活记录"],
        "visual": {
            "identity": {"hair_color": "棕色", "accessories": "圆框眼镜"},
            "background": "书房",
            "composition": "坐姿半身",
            "outfit": "米白衬衫",
        },
        "sources": [],
    }


def make_payload():
    return {
        "profile": {
            "required_tags": ["#阅读"],
            "forbidden_phrases": ["百分之百保证"],
            "max_body_chars": 300,
            "identity_anchors": {"hair_color": "棕色", "accessories": "圆框眼镜"},
            "min_visual_changes": 2,
        },
        "posts": [make_post()],
        "history": [],
    }


class ContentAuditCLI(unittest.TestCase):
    """Exercise actual process exits, on-disk input/output and boundary data."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="content-audit-")
        self.addCleanup(self.temp.cleanup)
        self.cwd = Path(self.temp.name)
        self.input_path = self.cwd / "输入 样本.json"
        self.output_path = self.cwd / "审稿 结果.json"

    def run_cli(self, payload=None, *, raw=None, output=False, extra_args=()):
        self.assertTrue(SCRIPT.is_file(), f"Implementation missing: {SCRIPT}")
        if raw is None:
            raw = json.dumps(make_payload() if payload is None else payload, ensure_ascii=False)
        self.input_path.write_text(raw, encoding="utf-8")
        command = [sys.executable, str(SCRIPT), "--input", str(self.input_path)]
        if output:
            command += ["--output", str(self.output_path)]
        command += list(extra_args)
        environment = dict(os.environ)
        environment["PYTHONIOENCODING"] = "utf-8"
        result = subprocess.run(
            command, cwd=self.cwd, env=environment,
            capture_output=True, encoding="utf-8", timeout=20, check=False,
        )
        self.assertNotIn("Traceback (most recent call last)", result.stdout + result.stderr)
        return result

    def report(self, result, *, output=False):
        text = self.output_path.read_text(encoding="utf-8") if output else result.stdout
        try:
            data = json.loads(text)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            self.fail(f"Expected machine-readable report, got {text!r}; stderr={result.stderr!r}: {exc}")
        self.assertIn("schema_version", data)
        self.assertIsInstance(data.get("findings"), list)
        summary = data["summary"]
        self.assertIsInstance(summary["passed"], bool)
        for count in ("post_count", "error_count", "warning_count"):
            self.assertIs(type(summary[count]), int)
            self.assertGreaterEqual(summary[count], 0)
        errors = [finding for finding in data["findings"] if finding["severity"] == "error"]
        warnings = [finding for finding in data["findings"] if finding["severity"] == "warning"]
        self.assertEqual(summary["error_count"], len(errors))
        self.assertEqual(summary["warning_count"], len(warnings))
        self.assertEqual(summary["passed"], not errors)
        self.assertEqual(result.returncode, 1 if errors else 0)
        for finding in data["findings"]:
            self.assertIsInstance(finding["code"], str)
            self.assertTrue(finding["message"])
            self.assertIn("post_id", finding)
        return data

    def test_valid_document_reports_pass(self):
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        data = self.report(result)
        self.assertEqual(data["summary"]["post_count"], 1)
        self.assertTrue(data["summary"]["passed"])

    def test_unicode_and_space_paths_write_explicit_output(self):
        result = self.run_cli(output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = self.report(result, output=True)
        self.assertTrue(data["summary"]["passed"])
        self.assertEqual(json.loads(self.input_path.read_text(encoding="utf-8")), make_payload())

    def test_required_tag_missing_is_error(self):
        payload = make_payload()
        payload["posts"][0]["tags"] = ["#生活记录"]
        data = self.report(self.run_cli(payload))
        self.assertGreater(data["summary"]["error_count"], 0)
        self.assertTrue(any("阅读" in item["message"] for item in data["findings"]))

    def test_forbidden_phrase_in_body_is_error(self):
        payload = make_payload()
        payload["posts"][0]["body"] += "百分之百保证每天进步。"
        data = self.report(self.run_cli(payload))
        self.assertGreater(data["summary"]["error_count"], 0)

    def test_forbidden_phrase_in_title_is_error(self):
        payload = make_payload()
        payload["posts"][0]["title"] = "百分之百保证提高效率"
        data = self.report(self.run_cli(payload))
        self.assertGreater(data["summary"]["error_count"], 0)

    def test_user_selected_length_limit(self):
        for body, expected_error in [("甲乙丙丁戊", False), ("甲乙丙丁戊己", True)]:
            with self.subTest(body=body):
                payload = make_payload()
                payload["profile"]["max_body_chars"] = 5
                payload["posts"][0]["body"] = body
                data = self.report(self.run_cli(payload))
                self.assertEqual(data["summary"]["error_count"] > 0, expected_error)

    def test_changed_identity_anchor_is_error(self):
        payload = make_payload()
        payload["posts"][0]["visual"]["identity"]["hair_color"] = "黑色"
        data = self.report(self.run_cli(payload))
        self.assertGreater(data["summary"]["error_count"], 0)

    def test_missing_visual_is_reported_not_silently_accepted(self):
        payload = make_payload()
        del payload["posts"][0]["visual"]
        data = self.report(self.run_cli(payload))
        self.assertTrue(data["findings"], "A requested visual check must disclose missing visual data")

    def test_same_visual_combination_is_reported(self):
        payload = make_payload()
        second = make_post("second", "雨后去公园散步，观察叶片上的水滴，沿着小路慢慢走完一圈。")
        second["title"] = "雨后的公园"
        payload["posts"].append(second)
        data = self.report(self.run_cli(payload))
        self.assertTrue(any(item.get("related_post_id") == "first" for item in data["findings"]))

    def test_two_visual_changes_allow_distinct_posts(self):
        payload = make_payload()
        second = make_post("second", "雨后去公园散步，观察叶片上的水滴，沿着小路慢慢走完一圈。")
        second["title"] = "雨后的公园"
        second["visual"].update(background="公园", composition="站姿全身", outfit="蓝色外套")
        payload["posts"].append(second)
        data = self.report(self.run_cli(payload))
        self.assertTrue(data["summary"]["passed"])
        self.assertEqual(data["summary"]["warning_count"], 0)

    def test_historical_exact_duplicate_with_same_id_is_detected(self):
        payload = make_payload()
        payload["history"] = [copy.deepcopy(payload["posts"][0])]
        data = self.report(self.run_cli(payload))
        self.assertTrue(data["findings"], "Same id across history must not bypass comparison")
        self.assertTrue(any(item.get("related_post_id") == "first" for item in data["findings"]))

    def test_long_chinese_near_duplicate_is_detected(self):
        body = (
            "周末整理书桌时，我把暂时用不到的东西放进抽屉，给常看的书留出位置。"
            "桌面空下来之后，读书前不用再花时间寻找笔和便签。"
            "我给自己安排了二十分钟，只读一小节，遇到喜欢的句子就写下来。"
            "这种安排没有让生活突然改变，却让我更愿意在晚上关掉手机，安静地坐一会儿。"
        )
        payload = make_payload()
        payload["posts"][0]["body"] = body
        old = make_post("older", body.replace("周末", "星期天").replace("二十分钟", "十五分钟"))
        old["title"] = "留给自己的阅读时间"
        old["visual"].update(background="阳台", composition="背影远景", outfit="绿色毛衣")
        payload["history"] = [old]
        data = self.report(self.run_cli(payload))
        self.assertTrue(any(item.get("related_post_id") == "older" for item in data["findings"]))

    def test_distinct_short_posts_and_same_tags_are_not_duplicates(self):
        payload = make_payload()
        payload["posts"][0].update(title="清晨", body="早上好，今天去读书。")
        second = make_post("second", "晚上好，明天去散步。")
        second["title"] = "夜晚"
        second["visual"].update(background="河边", composition="背影全身", outfit="运动服")
        payload["posts"].append(second)
        data = self.report(self.run_cli(payload))
        self.assertTrue(data["summary"]["passed"])
        self.assertTrue(all(item["code"] == "TEXT_TOO_SHORT" for item in data["findings"]))

    def test_malformed_json_has_clear_failure(self):
        result = self.run_cli(raw='{"posts": [')
        self.assertEqual(result.returncode, 2)
        self.assertTrue((result.stderr + result.stdout).strip())

    def test_nonfinite_json_numbers_are_rejected(self):
        for bad in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(number=bad):
                payload = json.dumps(make_payload(), ensure_ascii=False).replace('"max_body_chars": 300', '"max_body_chars": ' + bad)
                result = self.run_cli(raw=payload)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)

    def test_invalid_types_are_rejected(self):
        cases = []
        for key, value in (("max_body_chars", True), ("min_visual_changes", False),
                           ("max_body_chars", "300"), ("min_visual_changes", 2.5),
                           ("required_tags", "#阅读"), ("required_tags", ["#阅读", 17]),
                           ("forbidden_phrases", [None]), ("identity_anchors", ["棕色"])):
            payload = make_payload()
            payload["profile"][key] = value
            cases.append((f"profile.{key}={value!r}", payload))
        for key, value in (("posts", []), ("posts", {}), ("posts", [None]), ("history", {}), ("profile", [])):
            payload = make_payload()
            payload[key] = value
            cases.append((f"{key}={value!r}", payload))
        for key, value in (("body", 7), ("title", []), ("tags", [7]), ("visual", [])):
            payload = make_payload()
            payload["posts"][0][key] = value
            cases.append((f"post.{key}={value!r}", payload))
        cases.extend([("list root", []), ("null root", None)])
        for label, payload in cases:
            with self.subTest(case=label):
                result = self.run_cli(raw=json.dumps(payload, ensure_ascii=False))
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertTrue((result.stderr + result.stdout).strip())

    def test_omitted_optional_profile_and_history_are_accepted(self):
        result = self.run_cli({"posts": [make_post()]})
        data = self.report(result)
        self.assertTrue(data["summary"]["passed"])

    def test_output_must_not_overwrite_input(self):
        payload = make_payload()
        expected = json.dumps(payload, ensure_ascii=False)
        result = self.run_cli(payload, extra_args=("--output", str(self.input_path)))
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.input_path.read_text(encoding="utf-8"), expected)

    def test_equivalent_output_path_must_not_overwrite_input(self):
        payload = make_payload()
        expected = json.dumps(payload, ensure_ascii=False)
        alias = str(self.cwd) + os.sep + "." + os.sep + self.input_path.name
        result = self.run_cli(payload, extra_args=("--output", alias))
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.input_path.read_text(encoding="utf-8"), expected)

    def test_hardlinked_output_must_not_overwrite_input(self):
        payload = make_payload()
        expected = json.dumps(payload, ensure_ascii=False)
        self.input_path.write_text(expected, encoding="utf-8")
        alias = self.cwd / "hardlink-output.json"
        try:
            os.link(self.input_path, alias)
        except OSError as exc:
            self.skipTest(f"Filesystem does not support hard links: {exc}")
        result = self.run_cli(payload, extra_args=("--output", str(alias)))
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.input_path.read_text(encoding="utf-8"), expected)
        self.assertEqual(alias.read_text(encoding="utf-8"), expected)

    def test_no_output_option_leaves_unrelated_files_untouched(self):
        canary = self.cwd / "private-canary.txt"
        secret = "synthetic-canary-DoNotRead-592731"
        canary.write_text(secret, encoding="utf-8")
        result = self.run_cli()
        self.report(result)
        self.assertNotIn(secret, result.stdout + result.stderr)
        self.assertEqual(canary.read_text(encoding="utf-8"), secret)
        self.assertEqual({path.name for path in self.cwd.iterdir()}, {canary.name, self.input_path.name})


if __name__ == "__main__":
    unittest.main()
