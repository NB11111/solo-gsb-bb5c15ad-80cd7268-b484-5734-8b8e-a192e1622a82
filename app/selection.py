"""预检前的解包条目筛选：include/exclude glob 规则（纯内存）。

规则按输入顺序逐条匹配**规范化后的归档路径**（NFC、段尾裁剪、正斜杠分隔），
比较在 ``str.casefold()`` 比较键上进行，与碰撞/层级判定一致（Windows 不区分
大小写）。最后一条命中的规则决定取舍；没有任何规则命中时默认 include。

通配语义（按路径段解释，段分隔符恒为 ``/``）：

* ``*``  匹配**单个段内**任意数量字符（含 0 个），不跨越 ``/``；
* ``?``  匹配单个段内的恰好 1 个字符，不跨越 ``/``；
* ``**`` 仅在整段位置（``**``、``a/**``、``**/b``、``a/**/b``）匹配
  0 个或多个路径段，可以跨多级目录。

非法规则（空模式、含反斜杠、前导/结尾 ``/``、空段或整段 ``**`` 与其他
字符混用）在请求校验阶段返回 422。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .normalize import NormalizedEntry

# 单段内 * / ? 的逐字符匹配；其余字符按字面量匹配。
_SEGMENT_TOKEN_RE = re.compile(r"[*?]|[^*?]+")

# 合法规则模式：非空段之间以单个 / 分隔，段不得为纯空白。
# 整段 ** 的额外校验在 compile_selection_pattern 中完成。
_VALID_PATTERN_RE = re.compile(r"^[^/\\]+(?:/[^/\\]+)*$")


class InvalidSelectionPattern(ValueError):
    """selection_rules 中的规则模式非法（映射为 422）。"""


@dataclass(frozen=True)
class CompiledRule:
    """一条编译后的筛选规则。"""

    index: int  # 1 开始的规则下标（与输入顺序一致）
    action: str  # "include" | "exclude"
    tokens: tuple[tuple[str, str], ...]  # 段序列；每段为 (kind, value)
    # kind: "seg" -> value 为段内 token 正则源码；"starstar" -> 跨段匹配


def _compile_segment(segment: str) -> str:
    """将单个路径段编译为完整匹配的正则源码（不跨 /）。"""
    parts: list[str] = []
    for token in _SEGMENT_TOKEN_RE.findall(segment):
        if token == "*":
            parts.append(r"[^/]*")
        elif token == "?":
            parts.append(r"[^/]")
        else:
            # 字面量做大小写折叠后匹配（与 comparison_key 的 casefold 一致，
            # 遵循 Windows 目标的不区分大小写语义）。
            parts.append(re.escape(token.casefold()))
    return "^" + "".join(parts) + r"\Z"


def compile_selection_pattern(pattern: str) -> tuple[tuple[str, str], ...]:
    """校验并编译一条 glob 模式，返回段 token 元组。

    非法模式抛 :class:`InvalidSelectionPattern`（由 schema 层转为 422）。
    """
    if not isinstance(pattern, str) or not _VALID_PATTERN_RE.match(pattern):
        raise InvalidSelectionPattern(
            "selection_rules 的 pattern 必须是非空、不含反斜杠、"
            "不以 / 开头或结尾、且不含空段的 / 分隔路径模式"
        )
    segments = pattern.split("/")
    tokens: list[tuple[str, str]] = []
    for segment in segments:
        if not segment.strip():
            raise InvalidSelectionPattern(
                "selection_rules 的 pattern 不允许空白段"
            )
        if "**" in segment and segment != "**":
            raise InvalidSelectionPattern(
                f"selection_rules 的 pattern 段 {segment!r} 非法：** 只能作为"
                "完整路径段出现（如 **、a/**、**/b、a/**/b），不得与其他"
                "字符混用"
            )
        if segment == "**":
            tokens.append(("starstar", "**"))
        else:
            tokens.append(("seg", _compile_segment(segment)))
    return tuple(tokens)


def _match_tokens(
    tokens: tuple[tuple[str, str], ...], parts: tuple[str, ...]
) -> bool:
    """glob 段序列对具体路径段序列的递归匹配（记忆化）。"""
    memo: dict[tuple[int, int], bool] = {}

    def walk(ti: int, pi: int) -> bool:
        key = (ti, pi)
        if key in memo:
            return memo[key]

        if ti == len(tokens):
            result = pi == len(parts)
        elif tokens[ti][0] == "starstar":
            # ** 匹配 0 个或多个剩余路径段。
            result = any(
                walk(ti + 1, skip) for skip in range(pi, len(parts) + 1)
            )
        elif pi == len(parts):
            result = False
        else:
            kind, value = tokens[ti]
            result = kind == "seg" and re.match(value, parts[pi]) is not None
            if result:
                result = walk(ti + 1, pi + 1)
        memo[key] = result
        return result

    return walk(0, 0)


@dataclass(frozen=True)
class SelectionResult:
    """单条目的筛选结果。"""

    selected: bool
    matched_rule_index: int | None  # 最后一条命中的规则下标（1 开始）；未命中为 None


def apply_selection_rules(
    entries: list[NormalizedEntry],
    rules: list[CompiledRule],
) -> dict[int, SelectionResult]:
    """按输入顺序逐条匹配，返回 entry.index -> SelectionResult。

    无规则时所有条目的 ``matched_rule_index`` 均为 None 且默认 include。
    匹配基于规范化条目的 casefold 比较键段。
    """
    results: dict[int, SelectionResult] = {}
    for entry in entries:
        parts = tuple(entry.key_parts)
        selected = True
        matched: int | None = None
        for rule in rules:
            if _match_tokens(rule.tokens, parts):
                selected = rule.action == "include"
                matched = rule.index
        results[entry.index] = SelectionResult(
            selected=selected, matched_rule_index=matched
        )
    return results
