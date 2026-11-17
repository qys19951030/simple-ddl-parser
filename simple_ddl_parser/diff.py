"""Diff two DDL scripts and build a migration plan.

This module compares two DDL texts (an old and a new version of a schema)
using the existing ``DDLParser`` and reports the differences on three levels:

1. table level - added and removed tables;
2. column level - added/removed columns and type/default/nullable changes;
3. index & constraint level - added/removed/changed definitions.

On top of the report it can generate migration statements for a target
dialect (currently ``mysql`` and ``mssql``).
"""

import json
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


def _parse_tables(
    ddl: str, dialect: str
) -> Dict[Tuple[Optional[str], Optional[str]], Dict]:
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


def _column_type_repr(column: Dict) -> str:
    """Render a column type with its size, e.g. ``VARCHAR(100)``."""
    base = str(column.get("type") or "").strip()
    size = column.get("size")
    if size is None or size == "":
        return base
    if isinstance(size, (list, tuple)):
        return f"{base}({', '.join(str(part) for part in size)})"
    return f"{base}({size})"


def _normalize_type_repr(type_repr: str) -> str:
    return " ".join(type_repr.split()).lower()


def _normalize_default(value) -> Optional[str]:
    if value is None:
        return None
    return str(value).strip()


def _diff_columns(old_columns: List[Dict], new_columns: List[Dict]) -> Dict:
    """Compare column lists of two versions of one table."""
    result = _empty_change_groups()
    old_by_name = {_normalize_identifier(col.get("name")): col for col in old_columns}
    new_by_name = {_normalize_identifier(col.get("name")): col for col in new_columns}

    for name in new_by_name.keys() - old_by_name.keys():
        result["added"].append(new_by_name[name])
    for name in old_by_name.keys() - new_by_name.keys():
        result["removed"].append(old_by_name[name])

    for name in sorted(old_by_name.keys() & new_by_name.keys()):
        old_col, new_col = old_by_name[name], new_by_name[name]
        changes = {}

        old_type = _column_type_repr(old_col)
        new_type = _column_type_repr(new_col)
        if _normalize_type_repr(old_type) != _normalize_type_repr(new_type):
            changes["type"] = {"old": old_type, "new": new_type}

        old_default = _normalize_default(old_col.get("default"))
        new_default = _normalize_default(new_col.get("default"))
        if old_default != new_default:
            changes["default"] = {"old": old_default, "new": new_default}

        old_nullable = bool(old_col.get("nullable", True))
        new_nullable = bool(new_col.get("nullable", True))
        if old_nullable != new_nullable:
            changes["nullable"] = {"old": old_nullable, "new": new_nullable}

        if changes:
            result["changed"].append(
                {
                    "column": new_col.get("name"),
                    "changes": changes,
                    "new_column": new_col,
                }
            )
    return result


def _freeze(value) -> str:
    """Hashable, stable representation of a comparable definition."""
    return json.dumps(value, sort_keys=True, default=str)


def _diff_named_definitions(old_items: List[Tuple], new_items: List[Tuple]) -> Dict:
    """Generic diff over ``(name, comparable_definition, payload)`` triples.

    Named items match by name; a name present on both sides with a different
    definition lands in ``changed`` (not in removed+added). Unnamed items
    match by definition only.
    """
    result = _empty_change_groups()

    old_named = {item[0]: item[1:] for item in old_items if item[0] is not None}
    new_named = {item[0]: item[1:] for item in new_items if item[0] is not None}

    for name in new_named.keys() - old_named.keys():
        result["added"].append(new_named[name][1])
    for name in old_named.keys() - new_named.keys():
        result["removed"].append(old_named[name][1])
    for name in sorted(old_named.keys() & new_named.keys()):
        old_definition, old_payload = old_named[name]
        new_definition, new_payload = new_named[name]
        if _freeze(old_definition) != _freeze(new_definition):
            result["changed"].append({"old": old_payload, "new": new_payload})

    old_unnamed = [item[1:] for item in old_items if item[0] is None]
    new_unnamed = [item[1:] for item in new_items if item[0] is None]
    matched = set()
    for old_definition, old_payload in old_unnamed:
        for index, (new_definition, _) in enumerate(new_unnamed):
            if index not in matched and _freeze(old_definition) == _freeze(
                new_definition
            ):
                matched.add(index)
                break
        else:
            result["removed"].append(old_payload)
    for index, (_, new_payload) in enumerate(new_unnamed):
        if index not in matched:
            result["added"].append(new_payload)
    return result


def _index_definition(index: Dict) -> Dict:
    return {
        "columns": [
            _normalize_identifier(column) for column in index.get("columns") or []
        ],
        "unique": bool(index.get("unique", False)),
        "clustered": bool(index.get("clustered", False)),
    }


def _index_payload(index: Dict) -> Dict:
    return {
        "index": index.get("index_name"),
        "definition": {
            "columns": list(index.get("columns") or []),
            "unique": bool(index.get("unique", False)),
            "clustered": bool(index.get("clustered", False)),
        },
    }


def _diff_indexes(old_indexes: List[Dict], new_indexes: List[Dict]) -> Dict:
    def to_item(index):
        name = _normalize_identifier(index.get("index_name"))
        return (name, _index_definition(index), _index_payload(index))

    return _diff_named_definitions(
        [to_item(index) for index in old_indexes],
        [to_item(index) for index in new_indexes],
    )


def _constraint_definition(category: str, item: Dict) -> Dict:
    if category in ("primary_keys", "uniques"):
        return {
            "columns": [
                _normalize_identifier(column) for column in item.get("columns") or []
            ]
        }
    if category == "checks":
        return {"statement": " ".join(str(item.get("statement") or "").split()).lower()}
    if category == "references":
        local = item.get("name")
        if isinstance(local, list):
            local = [_normalize_identifier(column) for column in local]
        else:
            local = _normalize_identifier(local)
        return {
            "table": _display_name(
                (
                    _normalize_identifier(item.get("schema")),
                    _normalize_identifier(item.get("table")),
                )
            ),
            "columns": [
                _normalize_identifier(column) for column in item.get("columns") or []
            ],
            "name": local,
            "on_delete": (item.get("on_delete") or "").upper() or None,
            "on_update": (item.get("on_update") or "").upper() or None,
        }
    return {key: value for key, value in item.items() if key != "constraint_name"}


def _constraint_payload(category: str, item: Dict) -> Dict:
    return {
        "constraint": item.get("constraint_name"),
        "category": category,
        "definition": {
            key: value for key, value in item.items() if key != "constraint_name"
        },
    }


def _flatten_constraints(table: Dict) -> List[Tuple]:
    items = []
    constraints = table.get("constraints") or {}
    for category in sorted(constraints):
        entries = constraints[category]
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            name = _normalize_identifier(entry.get("constraint_name"))
            items.append(
                (
                    (category, name) if name is not None else None,
                    (category, _constraint_definition(category, entry)),
                    _constraint_payload(category, entry),
                )
            )
    return items


def _diff_constraints(old_table: Dict, new_table: Dict) -> Dict:
    return _diff_named_definitions(
        _flatten_constraints(old_table), _flatten_constraints(new_table)
    )


def _diff_table(
    key: Tuple[Optional[str], Optional[str]], old: Dict, new: Dict
) -> Optional[Dict]:
    """Diff two versions of one table; return None when they are identical."""
    table_diff = {
        "name": _display_name(key),
        "schema": key[0],
        "table": key[1],
        "columns": _diff_columns(old.get("columns", []), new.get("columns", [])),
        "indexes": _diff_indexes(old.get("index", []), new.get("index", [])),
        "constraints": _diff_constraints(old, new),
    }
    has_changes = any(
        table_diff[group][kind]
        for group in ("columns", "indexes", "constraints")
        for kind in ("added", "removed", "changed")
    )
    return table_diff if has_changes else None


def _sql_table_name(table: Dict) -> str:
    schema = table.get("schema")
    name = str(table.get("table_name"))
    return f"{schema}.{name}" if schema else name


def _column_sql(column: Dict) -> str:
    parts = [str(column.get("name")), _column_type_repr(column)]
    if not column.get("nullable", True):
        parts.append("NOT NULL")
    default = column.get("default")
    if default is not None:
        parts.append(f"DEFAULT {default}")
    return " ".join(parts)


def _create_table_sql(table: Dict) -> str:
    lines = [f"  {_column_sql(column)}" for column in table.get("columns", [])]
    primary_key = table.get("primary_key") or []
    if primary_key:
        lines.append(f"  PRIMARY KEY ({', '.join(str(c) for c in primary_key)})")
    body = ",\n".join(lines)
    return f"CREATE TABLE {_sql_table_name(table)} (\n{body}\n);"


def _drop_index_sql(table_name: str, payload: Dict, dialect: str) -> Optional[str]:
    name = payload.get("index")
    if name is None:
        return None
    if dialect == "mssql":
        return f"DROP INDEX {name} ON {table_name};"
    return f"ALTER TABLE {table_name} DROP INDEX {name};"


def _drop_constraint_sql(table_name: str, payload: Dict, dialect: str) -> Optional[str]:
    name = payload.get("constraint")
    category = payload.get("category")
    if dialect == "mssql":
        if name is None:
            return None
        return f"ALTER TABLE {table_name} DROP CONSTRAINT {name};"
    if category == "primary_keys":
        return f"ALTER TABLE {table_name} DROP PRIMARY KEY;"
    if name is None:
        return None
    if category == "uniques":
        return f"ALTER TABLE {table_name} DROP INDEX {name};"
    if category == "checks":
        return f"ALTER TABLE {table_name} DROP CHECK {name};"
    if category == "references":
        return f"ALTER TABLE {table_name} DROP FOREIGN KEY {name};"
    return f"ALTER TABLE {table_name} DROP CONSTRAINT {name};"


def _add_index_sql(table_name: str, payload: Dict, dialect: str) -> Optional[str]:
    name = payload.get("index")
    definition = payload.get("definition") or {}
    columns = ", ".join(str(c) for c in definition.get("columns") or [])
    unique = "UNIQUE " if definition.get("unique") else ""
    if dialect == "mssql":
        if name is None:
            return None
        return f"CREATE {unique}INDEX {name} ON {table_name} ({columns});"
    if name is None:
        return f"ALTER TABLE {table_name} ADD {unique}INDEX ({columns});"
    return f"ALTER TABLE {table_name} ADD {unique}INDEX {name} ({columns});"


def _add_constraint_sql(table_name: str, payload: Dict, dialect: str) -> Optional[str]:
    name = payload.get("constraint")
    category = payload.get("category")
    definition = payload.get("definition") or {}
    named = f"CONSTRAINT {name} " if name else ""
    if category == "primary_keys":
        columns = ", ".join(str(c) for c in definition.get("columns") or [])
        return f"ALTER TABLE {table_name} ADD {named}PRIMARY KEY ({columns});"
    if category == "uniques":
        columns = ", ".join(str(c) for c in definition.get("columns") or [])
        return f"ALTER TABLE {table_name} ADD {named}UNIQUE ({columns});"
    if category == "checks":
        return (
            f"ALTER TABLE {table_name} ADD {named}"
            f"CHECK ({definition.get('statement')});"
        )
    if category == "references":
        local = definition.get("name")
        if isinstance(local, list):
            local = ", ".join(str(c) for c in local)
        ref_table = definition.get("table")
        if definition.get("schema"):
            ref_table = f"{definition['schema']}.{ref_table}"
        ref_columns = ", ".join(str(c) for c in definition.get("columns") or [])
        statement = (
            f"ALTER TABLE {table_name} ADD {named}FOREIGN KEY ({local}) "
            f"REFERENCES {ref_table} ({ref_columns})"
        )
        if definition.get("on_delete"):
            statement += f" ON DELETE {definition['on_delete']}"
        if definition.get("on_update"):
            statement += f" ON UPDATE {definition['on_update']}"
        return statement + ";"
    return None


def _alter_column_sqls(table_name: str, change: Dict, dialect: str) -> List[str]:
    """Render column modification; mssql splits type and default changes."""
    column = change["column"]
    changes = change["changes"]
    new_column = change["new_column"]
    if dialect == "mssql":
        statements = []
        if "type" in changes or "nullable" in changes:
            nullable = "NULL" if new_column.get("nullable", True) else "NOT NULL"
            statements.append(
                f"ALTER TABLE {table_name} ALTER COLUMN "
                f"{column} {_column_type_repr(new_column)} {nullable};"
            )
        if "default" in changes and changes["default"]["new"] is not None:
            statements.append(
                f"ALTER TABLE {table_name} ADD DEFAULT "
                f"{changes['default']['new']} FOR {column};"
            )
        return statements
    return [f"ALTER TABLE {table_name} MODIFY COLUMN {_column_sql(new_column)};"]


def _table_statements(table_diff: Dict, new_table: Dict, dialect: str) -> List[str]:
    """Migration statements for one changed table.

    Fixed order: drop indexes/constraints, alter columns, add columns,
    then add indexes/constraints.
    """
    table_name = _sql_table_name(new_table)
    drops, alters, column_adds, adds = [], [], [], []

    for payload in table_diff["indexes"]["removed"]:
        statement = _drop_index_sql(table_name, payload, dialect)
        if statement:
            drops.append(statement)
    for change in table_diff["indexes"]["changed"]:
        statement = _drop_index_sql(table_name, change["old"], dialect)
        if statement:
            drops.append(statement)
    for payload in table_diff["constraints"]["removed"]:
        statement = _drop_constraint_sql(table_name, payload, dialect)
        if statement:
            drops.append(statement)
    for change in table_diff["constraints"]["changed"]:
        statement = _drop_constraint_sql(table_name, change["old"], dialect)
        if statement:
            drops.append(statement)

    for column in table_diff["columns"]["removed"]:
        alters.append(f"ALTER TABLE {table_name} DROP COLUMN {column['name']};")
    for change in table_diff["columns"]["changed"]:
        alters.extend(_alter_column_sqls(table_name, change, dialect))

    add_keyword = "ADD COLUMN" if dialect == "mysql" else "ADD"
    for column in table_diff["columns"]["added"]:
        column_adds.append(
            f"ALTER TABLE {table_name} {add_keyword} {_column_sql(column)};"
        )

    for payload in table_diff["indexes"]["added"]:
        statement = _add_index_sql(table_name, payload, dialect)
        if statement:
            adds.append(statement)
    for change in table_diff["indexes"]["changed"]:
        statement = _add_index_sql(table_name, change["new"], dialect)
        if statement:
            adds.append(statement)
    for payload in table_diff["constraints"]["added"]:
        statement = _add_constraint_sql(table_name, payload, dialect)
        if statement:
            adds.append(statement)
    for change in table_diff["constraints"]["changed"]:
        statement = _add_constraint_sql(table_name, change["new"], dialect)
        if statement:
            adds.append(statement)

    return drops + alters + column_adds + adds


def _generate_statements(
    report: Dict, old_tables: Dict, new_tables: Dict, dialect: str
) -> List[Dict]:
    """Render migration statements for the target dialect.

    Returns a list of ``{"table": <normalized name>, "sql": <statement>}``
    entries so filtered reports can drop statements per table.
    """
    statements = []

    for removed in report["tables"]["removed"]:
        key = (removed["schema"], removed["table"])
        statements.append(
            {
                "table": removed["name"],
                "sql": f"DROP TABLE {_sql_table_name(old_tables[key])};",
            }
        )
    for added in report["tables"]["added"]:
        key = (added["schema"], added["table"])
        statements.append(
            {"table": added["name"], "sql": _create_table_sql(new_tables[key])}
        )
    for table_diff in report["tables"]["changed"]:
        key = (table_diff["schema"], table_diff["table"])
        for sql in _table_statements(table_diff, new_tables[key], dialect):
            statements.append({"table": table_diff["name"], "sql": sql})
    return statements


def _public_report(internal: Dict) -> Dict:
    """Strip internal statement metadata down to plain SQL strings."""
    return {
        "dialect": internal["dialect"],
        "tables": internal["tables"],
        "statements": [entry["sql"] for entry in internal["statements"]],
    }


def diff_ddls(old_ddl: str, new_ddl: str, dialect: str) -> Dict:
    """Compare two DDL scripts and return a difference report.

    Args:
        old_ddl: DDL text of the previous schema version.
        new_ddl: DDL text of the new schema version.
        dialect: target dialect, one of ``SUPPORTED_DIFF_DIALECTS``.

    Returns:
        A dict with keys ``dialect``, ``tables`` and ``statements``.
    """
    return _public_report(_build_report(old_ddl, new_ddl, dialect))


def _matches_table_filter(entry: Dict, wanted: set) -> bool:
    return entry["name"] in wanted or entry["table"] in wanted


def diff_tables(old_ddl: str, new_ddl: str, dialect: str, tables: List[str]) -> Dict:
    """Like ``diff_ddls`` but limited to the given tables.

    ``tables`` holds table names (optionally schema-qualified); names that
    do not match any table in the DDL scripts are silently ignored. The
    result is a filtered view of the same full report ``diff_ddls`` builds.
    """
    internal = _build_report(old_ddl, new_ddl, dialect)
    wanted = set()
    for name in tables:
        normalized = _normalize_table_name(name)
        if normalized:
            wanted.add(normalized)
            wanted.add(normalized.split(".")[-1])

    groups = internal["tables"]
    filtered = {
        "dialect": internal["dialect"],
        "tables": {
            kind: [
                entry for entry in groups[kind] if _matches_table_filter(entry, wanted)
            ]
            for kind in ("added", "removed", "changed")
        },
        "statements": [
            entry["sql"]
            for entry in internal["statements"]
            if entry["table"] in wanted or entry["table"].split(".")[-1] in wanted
        ],
    }
    return filtered


def diff_table_level(old_ddl: str, new_ddl: str, dialect: str) -> Dict:
    """Table-level view of the diff: added/removed/changed table names only.

    Built by filtering the same full report ``diff_ddls`` builds, without
    column/index/constraint details.
    """
    internal = _build_report(old_ddl, new_ddl, dialect)
    groups = internal["tables"]
    return {
        "dialect": internal["dialect"],
        "tables": {
            "added": [entry["name"] for entry in groups["added"]],
            "removed": [entry["name"] for entry in groups["removed"]],
            "changed": [entry["name"] for entry in groups["changed"]],
        },
    }
