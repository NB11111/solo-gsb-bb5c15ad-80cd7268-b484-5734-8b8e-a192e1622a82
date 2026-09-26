"""selection_rules 条目筛选的测试。"""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _call(entries, **extra):
    return client.post(
        "/api/v1/preflight", json={"entries": entries, **extra}
    )


def _by_index(resp):
    return {e["index"]: e for e in resp["entries"]}


# ---------------------------------------------------------------------------
# 基本筛选：include/exclude、未命中默认 include、最后命中规则生效
# ---------------------------------------------------------------------------


def test_exclude_basic_last_matching_rule_wins():
    entries = [
        {"path": "keep/a.txt", "type": "file"},
        {"path": "drop/b.log", "type": "file"},
        {"path": "drop/c.txt", "type": "file"},
        {"path": "other/d.log", "type": "file"},
    ]
    rules = [
        {"action": "exclude", "pattern": "drop/*"},
        {"action": "include", "pattern": "drop/c.txt"},  # 最后命中：c 被救回
    ]
    resp = _call(entries, selection_rules=rules).json()
    by = _by_index(resp)

    assert by[1]["selected"] is True
    assert by[1]["matched_rule_index"] is None  # 未命中任何规则 → 默认 include
    assert by[2]["selected"] is False
    assert by[2]["matched_rule_index"] == 1
    assert by[3]["selected"] is True
    assert by[3]["matched_rule_index"] == 2  # 最后命中的是规则 #2
    assert by[4]["selected"] is True
    assert by[4]["matched_rule_index"] is None

    summary = resp["summary"]
    assert summary["selected"] == 3
    assert summary["excluded"] == 1
    assert summary["total"] == 4


def test_default_include_when_no_rule_matches():
    entries = [{"path": "a/b/c.txt", "type": "file"}]
    rules = [{"action": "exclude", "pattern": "nomatch/*"}]
    resp = _call(entries, selection_rules=rules).json()
    by = _by_index(resp)
    assert by[1]["selected"] is True
    assert by[1]["matched_rule_index"] is None
    assert resp["summary"]["excluded"] == 0
    assert resp["summary"]["selected"] == 1


def test_exclude_then_include_prefix_ordering():
    # 先全排除 docs，再用更具体的 include 规则救回部分；** 跨多级。
    entries = [
        {"path": "docs/x.txt", "type": "file"},
        {"path": "docs/sub/y.md", "type": "file"},
        {"path": "docs/sub/deep/z.txt", "type": "file"},
        {"path": "top.txt", "type": "file"},
    ]
    rules = [
        {"action": "exclude", "pattern": "docs/**"},
        {"action": "include", "pattern": "docs/**/*.txt"},
    ]
    resp = _call(entries, selection_rules=rules).json()
    by = _by_index(resp)
    # docs/x.txt：** 可匹配 0 段，故同样命中 include 规则（最后命中为 include）。
    assert by[1]["selected"] is True
    assert by[1]["matched_rule_index"] == 2
    # y.md 只命中 exclude。
    assert by[2]["selected"] is False
    # deep/z.txt 同时命中两条，最后一条 include 生效。
    assert by[3]["selected"] is True
    assert by[3]["matched_rule_index"] == 2
    # 未命中任何规则：默认 include。
    assert by[4]["selected"] is True
    assert by[4]["matched_rule_index"] is None


# ---------------------------------------------------------------------------
# glob 语义：* 不跨 /、? 单字符、** 跨多级
# ---------------------------------------------------------------------------


def test_star_does_not_cross_slash():
    entries = [
        {"path": "a.txt", "type": "file"},
        {"path": "dir/a.txt", "type": "file"},
        {"path": "a.txt/x", "type": "file"},
    ]
    rules = [{"action": "exclude", "pattern": "*.txt"}]
    resp = _call(entries, selection_rules=rules).json()
    by = _by_index(resp)
    assert by[1]["selected"] is False
    assert by[2]["selected"] is True  # * 不跨 /，dir/a.txt 不匹配
    assert by[3]["selected"] is True


def test_star_matches_zero_or_more_chars_within_segment():
    entries = [
        {"path": "abc.txt", "type": "file"},
        {"path": ".txt", "type": "file"},  # * 可匹配 0 个字符
        {"path": "abc.txt2", "type": "file"},
        {"path": "d/abc.txt", "type": "file"},
    ]
    rules = [{"action": "exclude", "pattern": "*.txt"}]
    resp = _call(entries, selection_rules=rules).json()
    by = _by_index(resp)
    assert by[1]["selected"] is False
    assert by[2]["selected"] is False
    assert by[3]["selected"] is True
    assert by[4]["selected"] is True


def test_question_marks_single_character():
    entries = [
        {"path": "a1.txt", "type": "file"},
        {"path": "a12.txt", "type": "file"},  # 两个字符，? 只匹配一个
        {"path": "a/.txt", "type": "file"},  # 零个字符
        {"path": "b/a1.txt", "type": "file"},  # ? 不跨 /
    ]
    rules = [{"action": "exclude", "pattern": "a?.txt"}]
    resp = _call(entries, selection_rules=rules).json()
    by = _by_index(resp)
    assert by[1]["selected"] is False
    assert by[2]["selected"] is True
    assert by[3]["selected"] is True
    assert by[4]["selected"] is True


def test_double_star_matches_multiple_levels():
    entries = [
        {"path": "root.txt", "type": "file"},
        {"path": "a/x.txt", "type": "file"},
        {"path": "a/b/x.txt", "type": "file"},
        {"path": "a/b/c/x.txt", "type": "file"},
    ]
    rules = [{"action": "exclude", "pattern": "**/x.txt"}]
    resp = _call(entries, selection_rules=rules).json()
    by = _by_index(resp)
    assert by[1]["selected"] is True  # root.txt 不匹配
    assert by[2]["selected"] is False  # ** 匹配单段 a
    assert by[3]["selected"] is False  # ** 匹配 a/b
    assert by[4]["selected"] is False


def test_double_star_matches_zero_segments():
    entries = [
        {"path": "x.txt", "type": "file"},  # ** 匹配 0 段
        {"path": "a/x.txt", "type": "file"},
        {"path": "a/b/x.txt", "type": "file"},
    ]
    rules = [{"action": "exclude", "pattern": "**/x.txt"}]
    resp = _call(entries, selection_rules=rules).json()
    by = _by_index(resp)
    assert by[1]["selected"] is False
    assert by[2]["selected"] is False
    assert by[3]["selected"] is False


def test_double_star_only_pattern_matches_everything():
    entries = [
        {"path": "x", "type": "file"},
        {"path": "a/b/c", "type": "directory"},
    ]
    rules = [{"action": "exclude", "pattern": "**"}]
    resp = _call(entries, selection_rules=rules).json()
    by = _by_index(resp)
    assert by[1]["selected"] is False
    assert by[2]["selected"] is False
    assert resp["summary"]["excluded"] == 2


def test_middle_double_star_pattern():
    entries = [
        {"path": "a/b/c", "type": "file"},
        {"path": "a/x/y/c", "type": "file"},
        {"path": "a/c", "type": "file"},  # ** 匹配 0 段
        {"path": "x/b/c", "type": "file"},
    ]
    rules = [{"action": "exclude", "pattern": "a/**/c"}]
    resp = _call(entries, selection_rules=rules).json()
    by = _by_index(resp)
    assert by[1]["selected"] is False
    assert by[2]["selected"] is False
    assert by[3]["selected"] is False
    assert by[4]["selected"] is True


def test_pattern_matches_normalized_canonical_path():
    # 反斜杠分隔符、段尾点空格、大小写都先被规范化/折叠再匹配。
    entries = [
        {"path": "Docs\\ReadMe.txt", "type": "file"},
        {"path": "docs/readme.txt.  ", "type": "file"},
        {"path": "DOCS/other.TXT", "type": "file"},
        {"path": "bin/tool", "type": "file"},  # 目录名不同，不匹配
    ]
    rules = [{"action": "exclude", "pattern": "docs/*.txt"}]
    resp = _call(entries, selection_rules=rules).json()
    by = _by_index(resp)
    # 匹配在规范化路径（正斜杠、段尾裁剪）+ 大小写折叠上进行：.TXT 同样命中。
    assert by[1]["selected"] is False
    assert by[2]["selected"] is False
    assert by[3]["selected"] is False
    assert by[4]["selected"] is True


def test_pattern_case_insensitive_on_key():
    entries = [
        {"path": "DOCS/X.TXT", "type": "file"},
    ]
    rules = [{"action": "exclude", "pattern": "docs/x.txt"}]
    resp = _call(entries, selection_rules=rules).json()
    assert resp["entries"][0]["selected"] is False


# ---------------------------------------------------------------------------
# 被排除条目不参与碰撞 / 层级冲突 / 符号链接 / 资源预算 / repair
# ---------------------------------------------------------------------------


def test_excluded_entries_still_return_normalization_info():
    entries = [
        {"path": "Docs\\ReadMe.txt", "type": "file"},
        {"path": "/etc/passwd", "type": "file"},  # 自身规范化不安全
    ]
    rules = [{"action": "exclude", "pattern": "**"}]
    resp = _call(entries, selection_rules=rules).json()
    by = _by_index(resp)
    assert by[1]["selected"] is False
    assert by[1]["canonical_path"] == "Docs/ReadMe.txt"
    assert by[1]["comparison_key"] == "docs/readme.txt"
    assert by[1]["safe"] is True
    assert by[1]["collision_group"] is None
    assert by[1]["blocked_by_file_ancestors"] == []
    assert by[1]["blocks_descendants"] == []
    assert by[1]["issues"] == []
    # 不安全的规范化信息同样保留。
    assert by[2]["selected"] is False
    assert by[2]["safe"] is False
    assert by[2]["issues"][0]["code"] == "ABSOLUTE_PATH"
    # 全部排除时所有深度分析为空。
    assert resp["collision_groups"] == []
    assert resp["hierarchy_conflicts"] == []
    assert resp["symlink_analysis"] == {
        "links": [],
        "cycles": [],
        "hijacked_entries": [],
    }


def test_excluded_entries_removed_from_collision_groups():
    entries = [
        {"path": "a.txt", "type": "file"},
        {"path": "keep/dup.txt", "type": "file"},
        {"path": "drop/dup.txt", "type": "file"},  # 排除：与 #2 的碰撞消失
        {"path": "b.txt", "type": "file"},
    ]
    rules = [{"action": "exclude", "pattern": "drop/*"}]
    resp = _call(entries, selection_rules=rules).json()
    by = _by_index(resp)
    assert by[2]["selected"] is True
    assert by[2]["collision_group"] is None
    assert by[3]["selected"] is False
    assert by[3]["collision_group"] is None
    assert resp["summary"]["collisions"] == 0
    assert resp["summary"]["collision_entries"] == 0
    assert "COLLISION" not in [i["code"] for i in by[2]["issues"]]


def test_excluded_file_ancestor_does_not_block_descendants():
    entries = [
        {"path": "a/b", "type": "file"},  # 排除：不再阻挡后代
        {"path": "a/b/c.txt", "type": "file"},
    ]
    rules = [{"action": "exclude", "pattern": "a/b"}]
    resp = _call(entries, selection_rules=rules).json()
    by = _by_index(resp)
    assert by[1]["selected"] is False
    assert by[2]["selected"] is True
    assert by[2]["blocked_by_file_ancestors"] == []
    assert by[1]["blocks_descendants"] == []
    assert resp["summary"]["hierarchy_conflicts"] == 0
    assert resp["hierarchy_conflicts"] == []


def test_excluded_descendant_not_reported_in_hierarchy_conflict():
    entries = [
        {"path": "a/b", "type": "file"},
        {"path": "a/b/c.txt", "type": "file"},  # 排除后代：冲突对消失
    ]
    rules = [{"action": "exclude", "pattern": "a/b/c.txt"}]
    resp = _call(entries, selection_rules=rules).json()
    by = _by_index(resp)
    assert by[1]["blocks_descendants"] == []
    assert resp["summary"]["hierarchy_conflicts"] == 0


def test_excluded_symlinks_absent_from_analysis():
    entries = [
        {
            "path": "a/lnk",
            "type": "symlink",
            "link_target": "../../../etc/passwd",  # 本应越界
        },
        {"path": "a/x.txt", "type": "file"},  # 排除链接后不被劫持
        {"path": "ok", "type": "symlink", "link_target": "a/x.txt"},
    ]
    rules = [{"action": "exclude", "pattern": "a/**"}]
    resp = _call(entries, selection_rules=rules).json()
    by = _by_index(resp)
    assert by[1]["selected"] is False
    assert by[2]["selected"] is False
    assert by[3]["selected"] is True
    # 排除的链接不在分析中，也不产生 LINK_TARGET_ESCAPE 问题。
    assert [l["index"] for l in resp["symlink_analysis"]["links"]] == [3]
    assert "LINK_TARGET_ESCAPE" not in [i["code"] for i in by[1]["issues"]]
    assert by[2]["safe"] is True
    assert resp["symlink_analysis"]["hijacked_entries"] == []
    assert resp["summary"]["symlinks"] == 1


def test_excluded_link_breaks_chain_for_selected_link():
    # 选中链接的链途经被排除的链接时，按不存在该链接解析（止于其路径）。
    entries = [
        {"path": "a/lnk1", "type": "symlink", "link_target": "sub/lnk2"},
        {"path": "a/sub/lnk2", "type": "symlink", "link_target": "../data/f.txt"},
        {"path": "a/data/f.txt", "type": "file"},
    ]
    rules = [{"action": "exclude", "pattern": "a/sub/lnk2"}]
    resp = _call(entries, selection_rules=rules).json()
    links = {l["index"]: l for l in resp["symlink_analysis"]["links"]}
    assert [l["index"] for l in resp["symlink_analysis"]["links"]] == [1]
    # lnk2 视为不存在：lnk1 的终点就是 a/sub/lnk2 本身。
    assert links[1]["final_path"] == "a/sub/lnk2"
    assert [s["index"] for s in links[1]["chain"]] == [1, None]


def test_excluded_entries_skipped_in_resource_budgets():
    entries = [
        {
            "path": "big.bin",
            "type": "file",
            "uncompressed_bytes": 9_999,
        },
        {
            "path": "small.bin",
            "type": "file",
            "uncompressed_bytes": 10,
        },
    ]
    rules = [{"action": "exclude", "pattern": "big.bin"}]
    resp = _call(
        entries,
        selection_rules=rules,
        resource_limits={
            "max_file_uncompressed_bytes": 100,
            "max_total_uncompressed_bytes": 50,
        },
    ).json()
    by = _by_index(resp)
    # 排除的大文件既不标记超限，也不计入声明字节与整批总量。
    assert by[1]["selected"] is False
    assert by[1]["safe"] is True
    assert by[1]["issues"] == []
    assert by[2]["safe"] is True
    summary = resp["summary"]
    assert summary["declared_uncompressed_bytes"] == 10
    assert summary["resource_limits_exceeded"] is False


def test_excluded_entries_absent_from_repair_plan():
    entries = [
        {"path": "a.txt", "type": "file"},
        {"path": "keep/dup.txt", "type": "file"},
        {"path": "drop/dup.txt", "type": "file"},  # 排除：碰撞不再存在
        {"path": "a/b", "type": "file"},
        {"path": "a/b/c.txt", "type": "file"},  # 排除：层级冲突不再存在
    ]
    rules = [
        {"action": "exclude", "pattern": "drop/*"},
        {"action": "exclude", "pattern": "a/b/c.txt"},
    ]
    resp = _call(entries, selection_rules=rules, repair=True).json()
    plan = {e["index"]: e for e in resp["repair_plan"]["entries"]}
    # repair_plan 只含选中条目，下标保持原请求下标。
    assert sorted(plan) == [1, 2, 4]
    assert plan[1]["repaired_path"] == "a.txt"
    assert plan[1]["reasons"] == []
    assert plan[2]["repaired_path"] == "keep/dup.txt"
    assert plan[2]["reasons"] == []
    assert plan[4]["repaired_path"] == "a/b"
    rp = resp["repair_plan"]
    assert rp["renamed_count"] == 0
    assert rp["skipped_count"] == 0
    assert rp["collisions_resolved"] == 0
    assert rp["hierarchy_conflicts_resolved"] == 0


def test_original_indices_preserved_for_remaining_entries():
    entries = [
        {"path": "x1", "type": "file"},
        {"path": "x2", "type": "file"},  # 排除
        {"path": "x3", "type": "symlink", "link_target": "x1"},  # 排除
        {"path": "x4", "type": "file"},
    ]
    rules = [
        {"action": "exclude", "pattern": "x2"},
        {"action": "exclude", "pattern": "x3"},
    ]
    resp = _call(entries, selection_rules=rules).json()
    assert [e["index"] for e in resp["entries"]] == [1, 2, 3, 4]
    assert [l["index"] for l in resp["symlink_analysis"]["links"]] == []
    by = _by_index(resp)
    assert by[1]["selected"] is True
    assert by[2]["selected"] is False
    assert by[3]["selected"] is False
    assert by[4]["selected"] is True


# ---------------------------------------------------------------------------
# 422：非法规则
# ---------------------------------------------------------------------------


def test_invalid_patterns_return_422():
    bad_patterns = [
        "",  # 空模式
        "   ",  # 纯空白
        "/a/b",  # 前导 /
        "a/b/",  # 结尾 /
        "a//b",  # 空段
        "a\\b",  # 含反斜杠
        "a**/b",  # ** 与其他字符混用
        "a/b**",  # 同上
        "**x",  # ** 非独立段
        "a/***/b",  # 非独立段
    ]
    for pattern in bad_patterns:
        r = _call(
            [{"path": "a", "type": "file"}],
            selection_rules=[{"action": "exclude", "pattern": pattern}],
        )
        assert r.status_code == 422, pattern


def test_invalid_action_returns_422():
    r = _call(
        [{"path": "a", "type": "file"}],
        selection_rules=[{"action": "drop", "pattern": "*"}],
    )
    assert r.status_code == 422


def test_missing_pattern_or_action_returns_422():
    r = _call(
        [{"path": "a", "type": "file"}],
        selection_rules=[{"action": "exclude"}],
    )
    assert r.status_code == 422
    r = _call(
        [{"path": "a", "type": "file"}],
        selection_rules=[{"pattern": "*"}],
    )
    assert r.status_code == 422


def test_empty_selection_rules_list_is_allowed():
    r = _call(
        [{"path": "a", "type": "file"}], selection_rules=[]
    )
    assert r.status_code == 200
    resp = r.json()
    assert resp["entries"][0]["selected"] is True
    assert resp["entries"][0]["matched_rule_index"] is None
    assert resp["summary"]["selected"] == 1
    assert resp["summary"]["excluded"] == 0


def test_request_level_422_still_applies_to_excluded_entries():
    # 请求级校验（资源预算所需字段缺失）先于筛选，对将被排除的文件同样 422。
    r = _call(
        [{"path": "big", "type": "file"}],  # 缺 uncompressed_bytes
        selection_rules=[{"action": "exclude", "pattern": "big"}],
        resource_limits={"max_file_uncompressed_bytes": 1},
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# 兼容性：无 selection_rules 时响应与旧版一致
# ---------------------------------------------------------------------------


def test_no_selection_rules_keeps_response_compatible():
    entries = [
        {"path": "a.txt", "type": "file"},
        {"path": "A.TXT", "type": "file"},
        {"path": "a/b", "type": "file"},
        {"path": "a/b/c.txt", "type": "file"},
    ]
    resp = _call(entries).json()
    by = _by_index(resp)
    for e in resp["entries"]:
        assert e["selected"] is True
        assert e["matched_rule_index"] is None
    # selected == total，excluded == 0；其余统计照旧。
    summary = resp["summary"]
    assert summary["selected"] == 4
    assert summary["excluded"] == 0
    assert summary["collisions"] == 1
    assert summary["hierarchy_conflicts"] == 1
    assert by[1]["collision_group"] == 1


def test_excluded_entry_still_returns_link_target():
    # 被排除的符号链接仍回显 link_target 与规范化信息。
    entries = [
        {"path": "lnk", "type": "symlink", "link_target": "ok.txt"},
    ]
    rules = [{"action": "exclude", "pattern": "lnk"}]
    resp = _call(entries, selection_rules=rules).json()
    e = resp["entries"][0]
    assert e["selected"] is False
    assert e["matched_rule_index"] == 1
    assert e["link_target"] == "ok.txt"
    assert e["canonical_path"] == "lnk"
    assert resp["symlink_analysis"]["links"] == []
    assert resp["summary"]["symlinks"] == 0


# ---------------------------------------------------------------------------
# glob 匹配器单元测试（边界语义）
# ---------------------------------------------------------------------------


def test_glob_matcher_unit_semantics():
    from app.selection import _match_tokens, compile_selection_pattern

    def matches(pattern, path):
        parts = tuple(p.casefold() for p in path.split("/"))
        return _match_tokens(compile_selection_pattern(pattern), parts)

    # * 不跨 /；可匹配 0 个字符。
    assert matches("*.txt", "a.txt")
    assert matches("*.txt", ".txt")
    assert not matches("*.txt", "d/a.txt")
    assert not matches("a/*.txt", "a.txt")
    # ? 恰好一个字符。
    assert matches("a?c", "abc")
    assert not matches("a?c", "ac")
    assert not matches("a?c", "abbc")
    assert not matches("?", "a/b")
    # ** 跨多级、可匹配 0 段。
    assert matches("**", "a")
    assert matches("**", "a/b/c")
    assert matches("a/**", "a")
    assert matches("a/**", "a/b/c")
    assert not matches("a/**", "b/c")
    assert matches("**/b", "b")
    assert matches("**/b", "x/b")
    assert matches("**/b", "x/y/b")
    assert matches("a/**/b", "a/b")
    assert matches("a/**/b", "a/x/y/b")
    assert not matches("a/**/b", "a/x/c")
    # 通配符按字面量之外的字符（[ ] 等）做字面量匹配。
    assert matches("a[0].txt", "a[0].txt")
    assert not matches("a[0].txt", "a0.txt")
    # 空路径（规范化后无有效段）只能被 ** 命中。
    assert _match_tokens(compile_selection_pattern("**"), ())
    assert not _match_tokens(compile_selection_pattern("*"), ())


def test_matcher_handles_literal_meta_characters():
    # +、(、)、. 等正则元字符在 glob 中按字面量处理。
    entries = [
        {"path": "a (1).txt", "type": "file"},
        {"path": "a (2).txt", "type": "file"},
    ]
    rules = [{"action": "exclude", "pattern": "a (1).txt"}]
    resp = _call(entries, selection_rules=rules).json()
    by = _by_index(resp)
    assert by[1]["selected"] is False
    assert by[2]["selected"] is True
