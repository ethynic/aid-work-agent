"""Markers owned by dedicated plan APIs, excluded from ordinary task management."""

SPECIAL_PLAN_MARKERS = ('[wecom-conversation:daily]',)
RESERVED_MARKERS = SPECIAL_PLAN_MARKERS + ('[wecom-conversation:backfill]',)

# Python str.strip() whitespace, excluding LF because split_part already removes it.
_STRIP_CODEPOINTS = (9, 11, 12, 13, 28, 29, 30, 31, 32, 133, 160, 5760,
                     8192, 8193, 8194, 8195, 8196, 8197, 8198, 8199,
                     8200, 8201, 8202, 8232, 8233, 8239, 8287, 12288)
_STRIP_SQL = 'concat(' + ','.join(f'chr({codepoint})' for codepoint in _STRIP_CODEPOINTS) + ')'


def special_plan_marker(task_prompt):
    first_line = (task_prompt or '').partition('\n')[0].strip()
    return next((marker for marker in SPECIAL_PLAN_MARKERS
                 if first_line.startswith(marker)), None)


def ordinary_task_prompt_allowed(task_prompt):
    first_line = (task_prompt or '').partition('\n')[0].strip()
    return not any(first_line.startswith(marker) for marker in RESERVED_MARKERS)


def first_line_sql(column='task_prompt'):
    """Match the leading-whitespace part of Python first-line strip in PostgreSQL."""
    return f"ltrim(split_part({column}, E'\\n', 1), {_STRIP_SQL})"


def ordinary_task_sql(column='task_prompt'):
    # % 双写转义：该片段全部以 f-string 内嵌进带参数的 cursor.execute，
    # 裸 % 会被 psycopg2 当占位符解析（IndexError），必须用 %% 表示字面 %
    return ' AND '.join(f"{first_line_sql(column)} NOT LIKE '{marker}%%'"
                        for marker in SPECIAL_PLAN_MARKERS)
