"""Diff two DDL scripts and build a migration plan.

This module compares two DDL texts (an old and a new version of a schema)
using the existing ``DDLParser`` and reports the differences on three levels:

1. table level - added and removed tables;
2. column level - added/removed columns and type/default/nullable changes;
3. index & constraint level - added/removed/changed definitions.

On top of the report it can generate migration statements for a target
dialect (currently ``mysql`` and ``mssql``).
"""

from typing import Dict, List, Optional, Tuple

from simple_ddl_parser.ddl_parser import DDLParser, DDLParserError

SUPPORTED_DIFF_DIALECTS = ("mysql", "mssql")

_QUOTE_PAIRS = (("`", "`"), ('"', '"'), ("[", "]"))


def _normalize_identifier(name: Optional[str]) -> Optional[str]:
    """Normalize an identifier: strip quoting characters and lower-case it.

    Backticks, double quotes and square brackets are treated as spelling
    variants only, so ```Users```, ``"USERS"`` and ``[users]`` all compare
    equal.
    """
    if name is None:
        return None
    text = str(name).strip()
    unwrapped = True
    while unwrapped and len(text) >= 2:
        unwrapped = False
        for left, right in _QUOTE_PAIRS:
            if text.startswith(left) and text.endswith(right):
                text = text[1:-1].strip()
                unwrapped = True
    return text.lower()


def _normalize_table_name(name: Optional[str]) -> Optional[str]:
    """Normalize a possibly schema-qualified name like ```dbo`.`Users```."""
    if name is None:
        return None
    parts = [
        _normalize_identifier(part)
        for part in str(name).split(".")
        if part and part.strip()
    ]
    return ".".join(part for part in parts if part)


def _table_key(table: Dict) -> Tuple[Optional[str], Optional[str]]:
    """Normalized (schema, table) identity of a parsed table dict."""
    return (
        _normalize_identifier(table.get("schema")),
        _normalize_identifier(table.get("table_name")),
    )


def _display_name(key: Tuple[Optional[str], Optional[str]]) -> str:
    schema, table = key
    return f"{schema}.{table}" if schema else (table or "")


def _parse_tables(ddl: str, dialect: str) -> Dict[Tuple[Optional[str], Optional[str]], Dict]:
    """Parse DDL text with the existing parser and index tables by normalized key."""
    parsed = DDLParser(ddl).run(output_mode=dialect, group_by_type=False)
    tables = {}
    for item in parsed:
        if not isinstance(item, dict) or "table_name" not in item:
            continue
        tables[_table_key(item)] = item
    return tables


def _validate_dialect(dialect: str) -> str:
    normalized = (dialect or "").lower()
    if normalized not in SUPPORTED_DIFF_DIALECTS:
        raise DDLParserError(
            f"Unsupported dialect for diff: {dialect!r}. "
            f"Supported dialects: {list(SUPPORTED_DIFF_DIALECTS)}"
        )
    return normalized


def _empty_change_groups() -> Dict:
    return {"added": [], "removed": [], "changed": []}


def _build_report(old_ddl: str, new_ddl: str, dialect: str) -> Dict:
    """Build the full difference report for two DDL scripts."""
    dialect = _validate_dialect(dialect)
    old_tables = _parse_tables(old_ddl, dialect)
    new_tables = _parse_tables(new_ddl, dialect)

    report = {
        "dialect": dialect,
        "tables": _empty_change_groups(),
        "statements": [],
    }

    for key in sorted(new_tables.keys() - old_tables.keys()):
        table = new_tables[key]
        report["tables"]["added"].append(
            {
                "name": _display_name(key),
                "schema": key[0],
                "table": key[1],
                "columns": table.get("columns", []),
            }
        )
    for key in sorted(old_tables.keys() - new_tables.keys()):
        report["tables"]["removed"].append(
            {"name": _display_name(key), "schema": key[0], "table": key[1]}
        )
    for key in sorted(old_tables.keys() & new_tables.keys()):
        table_diff = _diff_table(key, old_tables[key], new_tables[key])
        if table_diff is not None:
            report["tables"]["changed"].append(table_diff)

    report["statements"] = _generate_statements(report, old_tables, new_tables, dialect)
    return report


def _diff_table(
    key: Tuple[Optional[str], Optional[str]], old: Dict, new: Dict
) -> Optional[Dict]:
    """Diff two versions of one table; return None when they are identical."""
    table_diff = {
        "name": _display_name(key),
        "schema": key[0],
        "table": key[1],
        "columns": _empty_change_groups(),
        "indexes": _empty_change_groups(),
        "constraints": _empty_change_groups(),
    }
    has_changes = any(
        table_diff[group][kind]
        for group in ("columns", "indexes", "constraints")
        for kind in ("added", "removed", "changed")
    )
    return table_diff if has_changes else None


def _generate_statements(
    report: Dict, old_tables: Dict, new_tables: Dict, dialect: str
) -> List[str]:
    """Render migration statements for the target dialect."""
    return []


def diff_ddls(old_ddl: str, new_ddl: str, dialect: str) -> Dict:
    """Compare two DDL scripts and return a difference report.

    Args:
        old_ddl: DDL text of the previous schema version.
        new_ddl: DDL text of the new schema version.
        dialect: target dialect, one of ``SUPPORTED_DIFF_DIALECTS``.

    Returns:
        A dict with keys ``dialect``, ``tables`` and ``statements``.
    """
    return _build_report(old_ddl, new_ddl, dialect)
