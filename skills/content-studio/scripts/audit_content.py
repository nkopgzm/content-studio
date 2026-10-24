#!/usr/bin/env python3
"""Audit draft content against explicit, local rules (Python 3.10+, standard library).

Usage::

    python audit_content.py --input drafts.json
    python audit_content.py --input drafts.json --output audit.json

Input is one UTF-8 JSON object with ``posts`` (a nonempty list), optional
``profile`` (an object), and optional ``history`` (a list of previous posts).
Each post requires a nonblank string ``body``. Optional fields are string
``id``/``title``, lists of strings ``tags``/``screen_text``, ``visual`` and
``sources``. screen_text defaults to [] and holds separate on-screen captions.
For short videos body contains spoken narration only; keep the storyboard in
a separate human-readable draft file, not inside this checker's input object.
Visuals have string ``background``, ``composition``, ``outfit``, and an
``identity`` mapping of arbitrary string keys to string values. Missing visual
fields and empty identity values are reported as unchecked, not as a successful
visual review. Missing optional fields should be omitted; null is an input
format error, not a missing-value marker. Sources
are strings or objects containing string values; they are NOT fetched/verified.

Profile fields:
* required_tags: list of tags; leading/trailing # and surrounding spaces are
  ignored, matching is NFKC and case-insensitive. Tags must appear in the
  post's tags list. A tag containing only #/spaces is invalid.
* forbidden_phrases: list of literal phrases, matched after NFKC/case folding
  and whitespace normalization, separately within title, body, each tag and
  each screen_text entry. Fields are never concatenated for matching.
  Normalized duplicates are checked once, retaining the first display value.
  Splitting words with extra spaces or paraphrasing may evade literal matching.
* max_body_chars: optional positive integer; Python Unicode code points in the
  ORIGINAL body, including spaces, line breaks and hashtags (not a platform
  character-limit guarantee). Separate screen_text is not included.
* identity_anchors: string-to-string mapping checked against visual.identity.
  Values are exact enums after normalization, not semantic descriptions.
* min_visual_changes: integer 0..3, default 2, for background/composition/outfit.
  0 disables visual-rotation comparison, but identity checks still run.

Text comparison uses body only; titles, tag lists and screen_text cannot cause
duplication. Whitespace/punctuation/case are normalized; hashtag words written
inside body remain part of the text (no automatic hashtag stripping).
Bodies below 16 normalized letters/digits are not compared; each such new post
gets one TEXT_TOO_SHORT warning when another batch post or history entry exists.
Identical bodies
of 16..39 characters receive SHORT_TEXT_MATCH (warning). At >=40 characters,
exact matches are errors; near matches are warnings when weighted character
trigram Jaccard similarity is >=0.78 and the length ratio is >=0.65.
This is a lexical heuristic, not semantic similarity or plagiarism detection.

Only new posts receive findings. Every new post is compared with prior posts
in the same batch and every history entry, including entries with the same id.
IDs are labels, never deduplication keys. History is a reference set, not itself
audited. Identity and visual comparison use normalized TEXT fields; no image
recognition is performed. No rule verifies factual accuracy, citations,
copyright, platform compliance, audience suitability, or creative quality.

Success output: schema_version, summary, findings, and scope. summary.passed
means zero errors in the checks actually run; it is NEVER publication approval.
Input/IO errors instead return an error object. Exit codes: 0 no content errors
(warnings may exist), 1 content errors found, 2 invalid input or file error.
No network requests, credentials, shell execution, or automatic posting.
The only writes are to an explicitly requested, NEW --output path. Existing
files, directories, symlinks (including dangling links), and hardlinks are
rejected. Exclusive creation also refuses a path created after the initial
check. The input file is never overwritten. A disk/write failure may leave a
partial newly created report; the CLI returns exit 2 and reports that failure.
"""

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import sys
import unicodedata


SCHEMA_VERSION = "1.0"
VISUAL_FIELDS = ("background", "composition", "outfit")
PROFILE_FIELDS = {
    "required_tags", "forbidden_phrases", "max_body_chars",
    "identity_anchors", "min_visual_changes",
}
POST_FIELDS = {"id", "title", "body", "tags", "screen_text", "visual", "sources"}
MAX_INPUT_BYTES = 5 * 1024 * 1024
MAX_ENTRIES = 1000
MAX_BODY_CHARS = 100000


class InputError(ValueError):
    """A caller-provided input or path does not meet the documented contract."""


def _reject_constant(value):
    raise InputError("JSON 不允许非有限数值：" + value)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InputError("JSON 对象含重复字段：" + repr(key))
        result[key] = value
    return result


def _object(value, path):
    if not isinstance(value, dict):
        raise InputError(path + " 必须是 JSON 对象")
    return value


def _known_keys(value, allowed, path):
    unexpected = sorted(set(value) - allowed)
    if unexpected:
        raise InputError(path + " 含未知字段：" + ", ".join(unexpected))


def _string(value, path, nonblank=False):
    if not isinstance(value, str):
        raise InputError(path + " 必须是字符串")
    if nonblank and not value.strip():
        raise InputError(path + " 不能为空")
    return value


def _strings(value, path):
    if not isinstance(value, list):
        raise InputError(path + " 必须是字符串数组")
    for index, item in enumerate(value):
        _string(item, "{}[{}]".format(path, index), nonblank=True)
    return value


def _integer(value, path, minimum, maximum=None):
    if type(value) is not int:  # bool is an int subclass, but not a setting.
        raise InputError(path + " 必须是整数（不能是布尔值或小数）")
    if value < minimum or (maximum is not None and value > maximum):
        bounds = str(minimum) + (".." + str(maximum) if maximum is not None else " 以上")
        raise InputError(path + " 必须在 " + bounds + " 范围内")
    return value


def _normal(value):
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _tag(value):
    return _normal(value).strip("# ")


def _identity(value, path, allow_empty=False):
    _object(value, path)
    for key, item in value.items():
        _string(key, path + " 的字段名", nonblank=True)
        _string(item, path + "." + key, nonblank=not allow_empty)
    return value


def _post(value, path, fallback_id):
    _object(value, path)
    _known_keys(value, POST_FIELDS, path)
    if "body" not in value:
        raise InputError(path + ".body 为必填字段")
    body = _string(value["body"], path + ".body", nonblank=True)
    if len(body) > MAX_BODY_CHARS:
        raise InputError(path + ".body 超过本工具单条 100000 字符的处理上限")
    post = {
        "id": _string(value.get("id", fallback_id), path + ".id", nonblank=True),
        "title": _string(value.get("title", ""), path + ".title"),
        "body": body,
        "tags": _strings(value.get("tags", []), path + ".tags"),
        "screen_text": _strings(value.get("screen_text", []), path + ".screen_text"),
        "visual": {},
    }
    for index, tag in enumerate(post["tags"]):
        if not _tag(tag):
            raise InputError("{}.tags[{}] 不能只有 # 或空白".format(path, index))
    if "visual" in value:
        visual = _object(value["visual"], path + ".visual")
        _known_keys(visual, set(VISUAL_FIELDS) | {"identity"}, path + ".visual")
        post["visual"] = dict(visual)
        for key in VISUAL_FIELDS:
            if key in visual:
                _string(visual[key], path + ".visual." + key)
        if "identity" in visual:
            _identity(visual["identity"], path + ".visual.identity", allow_empty=True)
    sources = value.get("sources", [])
    if not isinstance(sources, list):
        raise InputError(path + ".sources 必须是数组")
    for index, source in enumerate(sources):
        source_path = "{}.sources[{}]".format(path, index)
        if isinstance(source, str):
            _string(source, source_path, nonblank=True)
        elif isinstance(source, dict):
            if not source:
                raise InputError(source_path + " 不能是空对象")
            for key, item in source.items():
                _string(key, source_path + " 的字段名", nonblank=True)
                _string(item, source_path + "." + key, nonblank=True)
        else:
            raise InputError(source_path + " 必须是字符串或只含字符串字段的对象")
    return post


def validate_payload(payload):
    """Return a normalized profile, new posts, and history; never mutate input."""
    _object(payload, "根对象")
    _known_keys(payload, {"profile", "posts", "history"}, "根对象")
    profile = dict(_object(payload.get("profile", {}), "profile"))
    _known_keys(profile, PROFILE_FIELDS, "profile")
    profile["required_tags"] = _strings(profile.get("required_tags", []), "profile.required_tags")
    for index, tag in enumerate(profile["required_tags"]):
        if not _tag(tag):
            raise InputError("profile.required_tags[{}] 不能只有 # 或空白".format(index))
    profile["forbidden_phrases"] = _strings(profile.get("forbidden_phrases", []), "profile.forbidden_phrases")
    profile["identity_anchors"] = _identity(profile.get("identity_anchors", {}), "profile.identity_anchors")
    profile["min_visual_changes"] = _integer(profile.get("min_visual_changes", 2), "profile.min_visual_changes", 0, 3)
    if "max_body_chars" in profile:
        _integer(profile["max_body_chars"], "profile.max_body_chars", 1)
    posts = payload.get("posts")
    if not isinstance(posts, list) or not posts:
        raise InputError("posts 必须是非空数组")
    history = payload.get("history", [])
    if not isinstance(history, list):
        raise InputError("history 必须是数组")
    if len(posts) + len(history) > MAX_ENTRIES:
        raise InputError("posts 与 history 合计不能超过 1000 条")
    return (
        profile,
        [_post(item, "posts[{}]".format(i), "post-{}".format(i + 1)) for i, item in enumerate(posts)],
        [_post(item, "history[{}]".format(i), "history-{}".format(i + 1)) for i, item in enumerate(history)],
    )


def _body_key(body):
    text = unicodedata.normalize("NFKC", body).casefold()
    # Do not strip #words: without spaces, a Chinese paragraph can look like
    # one hashtag. Independent tag arrays are already excluded from comparison.
    return "".join(char for char in text if char.isalnum())


def _trigrams(text):
    return Counter(text[index:index + 3] for index in range(len(text) - 2))


def _similarity(first, second):
    intersection = sum((first & second).values())
    union = sum((first | second).values())
    return intersection / union if union else 0.0


def audit(payload):
    """Run deterministic checks and return a report, or raise InputError."""
    profile, posts, history = validate_payload(payload)
    findings = []

    def add(severity, code, post, message, related=None):
        finding = {"severity": severity, "code": code, "post_id": post["id"], "message": message}
        if related is not None:
            finding["related_post_id"] = related["id"]
        findings.append(finding)

    required_tags = {_tag(tag): tag for tag in profile["required_tags"]}
    forbidden_phrases = {}
    for phrase in profile["forbidden_phrases"]:
        forbidden_phrases.setdefault(_normal(phrase), phrase)
    previous_ids = set()
    for index, post in enumerate(posts):
        if post["id"] in previous_ids:
            add("warning", "DUPLICATE_ID", post, "本批第 {} 条的 id 重复；仍按条目位置执行比较，建议使用唯一 id。".format(index + 1))
        previous_ids.add(post["id"])
        present_tags = {_tag(tag) for tag in post["tags"]}
        for key, original in required_tags.items():
            if key not in present_tags:
                add("error", "MISSING_REQUIRED_TAG", post, "tags 缺少固定标签：" + original)
        normalized_fields = [_normal(value) for value in
                             [post["title"], post["body"]] + post["tags"] + post["screen_text"]]
        for normalized_phrase, phrase in forbidden_phrases.items():
            if any(normalized_phrase in value for value in normalized_fields):
                add("error", "FORBIDDEN_PHRASE", post, "检测到禁用短语：" + phrase)
        if "max_body_chars" in profile and len(post["body"]) > profile["max_body_chars"]:
            add("error", "BODY_TOO_LONG", post, "正文 {} 字符，超过用户设置的 {} 字符（包含空白和标签）。".format(len(post["body"]), profile["max_body_chars"]))

        identity = post["visual"].get("identity", {})
        for key, expected in profile["identity_anchors"].items():
            if key not in identity or not _normal(identity[key]):
                add("warning", "IDENTITY_UNCHECKED", post, "visual.identity.{} 缺失或为空，无法检查此身份锚点。".format(key))
            elif _normal(identity[key]) != _normal(expected):
                add("error", "IDENTITY_MISMATCH", post, "身份锚点 {} 应为 {!r}，实际描述为 {!r}。".format(key, expected, identity[key]))
        missing_visual = [key for key in VISUAL_FIELDS if not _normal(post["visual"].get(key, ""))]
        if missing_visual and profile["min_visual_changes"]:
            add("warning", "VISUAL_INCOMPLETE", post, "视觉轮换未检查：缺少 " + ", ".join(missing_visual) + "。")

    # Cache comparison keys/counters once. Label IDs never control which pairs run.
    def comparison_item(post, origin, index):
        body = _body_key(post["body"])
        visual = tuple(_normal(post["visual"].get(key, "")) for key in VISUAL_FIELDS)
        return {"post": post, "origin": origin, "index": index, "body": body,
                "grams": _trigrams(body) if len(body) >= 40 else None,
                "visual": visual, "visual_complete": all(visual)}

    history_items = [comparison_item(post, "历史", i + 1) for i, post in enumerate(history)]
    batch_items = [comparison_item(post, "本批", i + 1) for i, post in enumerate(posts)]
    for index, current in enumerate(batch_items):
        post = current["post"]
        candidates = batch_items[:index] + history_items
        if len(current["body"]) < 16 and (len(batch_items) > 1 or history_items):
            add("warning", "TEXT_TOO_SHORT", post, "正文规范化后不足 16 个字母或数字字符，无法可靠判断与其他本批作品或历史记录的重复，请人工检查。")
        skipped_history_visual = []
        for other in candidates:
            related = other["post"]
            label = "{}第 {} 条（{}）".format(other["origin"], other["index"], related["id"])
            first, second = current["body"], other["body"]
            if first == second and len(first) >= 40:
                add("error", "TEXT_DUPLICATE", post, "正文规范化后与 " + label + " 完全相同。", related)
            elif first == second and len(first) >= 16:
                add("warning", "SHORT_TEXT_MATCH", post, "短正文与 " + label + " 相同；篇幅不足以判断高重复，请人工检查。", related)
            elif len(first) >= 40 and len(second) >= 40:
                ratio = min(len(first), len(second)) / max(len(first), len(second))
                similarity = _similarity(current["grams"], other["grams"]) if ratio >= 0.65 else 0.0
                if similarity >= 0.78:
                    add("warning", "TEXT_SIMILAR", post, "正文与 {} 的字符三元组相似度为 {:.1%}；这是词面提示，需人工复核。".format(label, similarity), related)
            if not profile["min_visual_changes"] or not current["visual_complete"]:
                continue
            if not other["visual_complete"]:
                if other["origin"] == "历史":
                    skipped_history_visual.append(label)
                # Missing batch visuals already have their own explicit finding.
                continue
            changed = sum(left != right for left, right in zip(current["visual"], other["visual"]))
            if changed < profile["min_visual_changes"]:
                code = "VISUAL_REPEAT" if changed == 0 else "VISUAL_TOO_SIMILAR"
                add("error", code, post, "与 {} 相比，背景、构图、服装仅有 {} 项描述不同，要求至少 {} 项。只比较文字描述，不验证实际图片。".format(label, changed, profile["min_visual_changes"]), related)
        if skipped_history_visual:
            add("warning", "VISUAL_INCOMPLETE", post, "未与 {} 条视觉字段不全的历史记录比较：{}。".format(len(skipped_history_visual), "；".join(skipped_history_visual[:10]) + ("；其余略" if len(skipped_history_visual) > 10 else "")))

    error_count = sum(item["severity"] == "error" for item in findings)
    warning_count = sum(item["severity"] == "warning" for item in findings)
    return {
        "schema_version": SCHEMA_VERSION,
        "summary": {"post_count": len(posts), "error_count": error_count,
                    "warning_count": warning_count, "passed": error_count == 0},
        "findings": findings,
        "scope": {
            "meaning_of_passed": "仅表示已执行的本地规则未发现 error；warning 和未检查项仍需处理，不代表可发布。",
            "text_method": "正文字符三元组加权 Jaccard；不剥离正文里的标签词；短文本仅有限比较，独立标签列表、标题和 screen_text 不参与相似度。",
            "forbidden_phrase_fields": ["title", "body", "tags", "screen_text"],
            "forbidden_phrase_method": "各字段内独立字面匹配，不跨字段拼接；不识别插空拆字或同义改写。",
            "visual_method": "仅比较用户填写的身份、背景、构图、服装文字；没有检查图片。",
            "not_checked": ["事实真伪", "引用是否支持结论", "版权或抄袭认定", "平台最新规则", "语义重复", "实际图片一致性", "创作质量"],
            "history_count": len(history),
            "configured_checks": {"required_tags": bool(required_tags),
                                  "forbidden_phrases": bool(profile["forbidden_phrases"]),
                                  "max_body_chars": "max_body_chars" in profile,
                                  "identity_anchors": bool(profile["identity_anchors"]),
                                  "visual_rotation": bool(profile["min_visual_changes"])},
        },
    }


def _read_payload(path):
    try:
        with path.open("rb") as handle:
            data = handle.read(MAX_INPUT_BYTES + 1)
        if len(data) > MAX_INPUT_BYTES:
            raise InputError("输入 JSON 超过 5 MiB 的处理上限")
        return json.loads(data.decode("utf-8-sig"), parse_constant=_reject_constant,
                          object_pairs_hook=_unique_object)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise InputError("输入必须是有效的 UTF-8 JSON：" + str(exc)) from exc


def _check_distinct_paths(input_path, output_path):
    # lexists includes dangling symlinks; exists alone would miss them.
    if os.path.lexists(output_path):
        raise InputError("--output 已存在；请使用新的报告路径，不会覆盖文件、目录或链接")
    if input_path.resolve() == output_path.resolve():
        raise InputError("--output 不能覆盖 --input 文件")


def _write_report(path, text):
    # x uses exclusive creation: no check-then-replace race can overwrite a file.
    # Do not remove anything on failure: a partial NEW report may remain, while
    # every pre-existing file (including a competing writer's file) stays intact.
    try:
        with path.open(mode="x", encoding="utf-8", newline="\n") as handle:
            handle.write(text + "\n")
    except FileExistsError as exc:
        raise InputError("--output 已存在或在检查后被创建；未覆盖该路径") from exc
    except OSError as exc:
        raise InputError("报告未完整保存：{}；若新文件已创建，可能留下不完整报告：{}".format(exc, path)) from exc


def main(argv=None):
    parser = argparse.ArgumentParser(description="离线检查内容草稿；不联网、不发布、不代替人工审稿。")
    parser.add_argument("--input", required=True, type=Path, help="输入 JSON 文件")
    parser.add_argument("--output", type=Path, help="可选的新报告路径；拒绝已存在文件、目录和链接，父目录必须已存在")
    args = parser.parse_args(argv)
    try:
        if args.output is not None:
            _check_distinct_paths(args.input, args.output)
        report = audit(_read_payload(args.input))
        text = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)
        if args.output is not None:
            _write_report(args.output, text)
        print(text)
        return 0 if report["summary"]["passed"] else 1
    except (InputError, OSError, RecursionError, ValueError) as exc:
        error = {"schema_version": SCHEMA_VERSION,
                 "error": {"code": "INVALID_INPUT", "message": str(exc)}}
        print(json.dumps(error, ensure_ascii=False, allow_nan=False))
        print("审稿未运行或报告未保存：" + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    # Keep CLI JSON usable in Windows terminals and pipes regardless of locale.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    sys.exit(main())
