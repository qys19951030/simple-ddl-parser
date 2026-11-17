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
