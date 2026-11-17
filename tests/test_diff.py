"""Tests for simple_ddl_parser.diff (DDL vs DDL comparison)."""

import pytest

from simple_ddl_parser.diff import diff_ddls
from simple_ddl_parser.ddl_parser import DDLParserError


def test_diff_module_importable():
    assert callable(diff_ddls)


class TestTableLevel:
    def test_added_table(self):
        report = diff_ddls("", "CREATE TABLE users (id INT);", "mysql")
        assert [t["name"] for t in report["tables"]["added"]] == ["users"]
        assert report["tables"]["removed"] == []
        assert report["tables"]["changed"] == []

    def test_removed_table(self):
        report = diff_ddls("CREATE TABLE users (id INT);", "", "mysql")
        assert [t["name"] for t in report["tables"]["removed"]] == ["users"]
        assert report["tables"]["added"] == []

    def test_added_table_columns_included(self):
        report = diff_ddls(
            "", "CREATE TABLE users (id INT, name VARCHAR(10));", "mysql"
        )
        added = report["tables"]["added"][0]
        assert [c["name"] for c in added["columns"]] == ["id", "name"]

    def test_quoting_and_case_differences_do_not_matter(self):
        old = "CREATE TABLE `myschema`.`Users` (id INT);"
        new = 'CREATE TABLE "MYSCHEMA"."users" (id INT);'
        report = diff_ddls(old, new, "mysql")
        assert report["tables"]["added"] == []
        assert report["tables"]["removed"] == []
        assert report["tables"]["changed"] == []

    def test_schema_prefix_normalized(self):
        report = diff_ddls(
            "", "CREATE TABLE `dbo`.`Orders` (id INT);", "mssql"
        )
        added = report["tables"]["added"][0]
        assert added["name"] == "dbo.orders"
        assert added["schema"] == "dbo"
        assert added["table"] == "orders"

    def test_unsupported_dialect_raises(self):
        with pytest.raises(DDLParserError):
            diff_ddls("", "", "oracle")


def _changed_columns(report, table="users"):
    changed = {t["table"]: t for t in report["tables"]["changed"]}
    return changed[table]["columns"]


class TestColumnLevel:
    def test_added_column(self):
        old = "CREATE TABLE users (id INT);"
        new = "CREATE TABLE users (id INT, name VARCHAR(50));"
        columns = _changed_columns(diff_ddls(old, new, "mysql"))
        assert [c["name"] for c in columns["added"]] == ["name"]
        assert columns["removed"] == []
        assert columns["changed"] == []

    def test_removed_column(self):
        old = "CREATE TABLE users (id INT, name VARCHAR(50));"
        new = "CREATE TABLE users (id INT);"
        columns = _changed_columns(diff_ddls(old, new, "mysql"))
        assert [c["name"] for c in columns["removed"]] == ["name"]

    def test_type_change(self):
        old = "CREATE TABLE users (id INT);"
        new = "CREATE TABLE users (id BIGINT);"
        columns = _changed_columns(diff_ddls(old, new, "mysql"))
        assert columns["changed"] == [
            {"column": "id", "changes": {"type": {"old": "INT", "new": "BIGINT"}}}
        ]

    def test_type_size_change(self):
        old = "CREATE TABLE users (name VARCHAR(50));"
        new = "CREATE TABLE users (name VARCHAR(100));"
        columns = _changed_columns(diff_ddls(old, new, "mysql"))
        assert columns["changed"][0]["changes"]["type"] == {
            "old": "VARCHAR(50)",
            "new": "VARCHAR(100)",
        }

    def test_type_case_difference_is_not_a_change(self):
        old = "CREATE TABLE users (name varchar(50));"
        new = "CREATE TABLE users (name VARCHAR(50));"
        report = diff_ddls(old, new, "mysql")
        assert report["tables"]["changed"] == []

    def test_default_change(self):
        old = "CREATE TABLE users (status INT DEFAULT 0);"
        new = "CREATE TABLE users (status INT DEFAULT 1);"
        columns = _changed_columns(diff_ddls(old, new, "mysql"))
        assert columns["changed"][0]["changes"]["default"] == {
            "old": "0",
            "new": "1",
        }

    def test_default_added_and_removed(self):
        old = "CREATE TABLE users (status INT);"
        new = "CREATE TABLE users (status INT DEFAULT 0);"
        columns = _changed_columns(diff_ddls(old, new, "mysql"))
        assert columns["changed"][0]["changes"]["default"] == {
            "old": None,
            "new": "0",
        }
        columns = _changed_columns(diff_ddls(new, old, "mysql"))
        assert columns["changed"][0]["changes"]["default"] == {
            "old": "0",
            "new": None,
        }

    def test_nullable_change(self):
        old = "CREATE TABLE users (name VARCHAR(50));"
        new = "CREATE TABLE users (name VARCHAR(50) NOT NULL);"
        columns = _changed_columns(diff_ddls(old, new, "mysql"))
        assert columns["changed"][0]["changes"]["nullable"] == {
            "old": True,
            "new": False,
        }

    def test_multiple_changes_on_one_column(self):
        old = "CREATE TABLE users (name VARCHAR(50) DEFAULT 'a');"
        new = "CREATE TABLE users (name VARCHAR(100) NOT NULL DEFAULT 'b');"
        columns = _changed_columns(diff_ddls(old, new, "mysql"))
        changes = columns["changed"][0]["changes"]
        assert changes["type"] == {"old": "VARCHAR(50)", "new": "VARCHAR(100)"}
        assert changes["default"] == {"old": "'a'", "new": "'b'"}
        assert changes["nullable"] == {"old": True, "new": False}

    def test_identical_tables_produce_no_changes(self):
        ddl = "CREATE TABLE users (id INT, name VARCHAR(50));"
        report = diff_ddls(ddl, ddl, "mysql")
        assert report["tables"]["changed"] == []
