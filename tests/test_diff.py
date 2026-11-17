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


def _changed_table(report, table="users"):
    changed = {t["table"]: t for t in report["tables"]["changed"]}
    return changed[table]


class TestIndexLevel:
    def test_added_index(self):
        old = "CREATE TABLE users (id INT, age INT);"
        new = "CREATE TABLE users (id INT, age INT, INDEX idx_age (age));"
        indexes = _changed_table(diff_ddls(old, new, "mysql"))["indexes"]
        assert [i["index"] for i in indexes["added"]] == ["idx_age"]
        assert indexes["removed"] == []
        assert indexes["changed"] == []

    def test_removed_index(self):
        old = "CREATE TABLE users (id INT, age INT, INDEX idx_age (age));"
        new = "CREATE TABLE users (id INT, age INT);"
        indexes = _changed_table(diff_ddls(old, new, "mysql"))["indexes"]
        assert [i["index"] for i in indexes["removed"]] == ["idx_age"]

    def test_changed_index_is_separate_from_add_remove(self):
        old = "CREATE TABLE users (a INT, b INT, INDEX idx_x (a));"
        new = "CREATE TABLE users (a INT, b INT, INDEX idx_x (a, b));"
        indexes = _changed_table(diff_ddls(old, new, "mysql"))["indexes"]
        assert indexes["added"] == []
        assert indexes["removed"] == []
        assert len(indexes["changed"]) == 1
        change = indexes["changed"][0]
        assert change["old"]["index"] == "idx_x"
        assert change["old"]["definition"]["columns"] == ["a"]
        assert change["new"]["definition"]["columns"] == ["a", "b"]

    def test_unique_flag_change_on_index(self):
        old = "CREATE TABLE users (a INT, INDEX idx_x (a));"
        new = "CREATE TABLE users (a INT, UNIQUE INDEX idx_x (a));"
        indexes = _changed_table(diff_ddls(old, new, "mysql"))["indexes"]
        assert len(indexes["changed"]) == 1
        assert indexes["changed"][0]["old"]["definition"]["unique"] is False
        assert indexes["changed"][0]["new"]["definition"]["unique"] is True

    def test_index_name_case_insensitive(self):
        old = "CREATE TABLE users (a INT, INDEX IDX_AGE (a));"
        new = "CREATE TABLE users (a INT, INDEX idx_age (a));"
        report = diff_ddls(old, new, "mysql")
        assert report["tables"]["changed"] == []


class TestConstraintLevel:
    def test_added_constraint(self):
        old = "CREATE TABLE users (id INT, name VARCHAR(50));"
        new = (
            "CREATE TABLE users (id INT, name VARCHAR(50), "
            "CONSTRAINT uq_name UNIQUE (name));"
        )
        constraints = _changed_table(diff_ddls(old, new, "mysql"))["constraints"]
        assert [c["constraint"] for c in constraints["added"]] == ["uq_name"]
        assert constraints["added"][0]["category"] == "uniques"

    def test_removed_constraint(self):
        old = (
            "CREATE TABLE users (id INT, name VARCHAR(50), "
            "CONSTRAINT uq_name UNIQUE (name));"
        )
        new = "CREATE TABLE users (id INT, name VARCHAR(50));"
        constraints = _changed_table(diff_ddls(old, new, "mysql"))["constraints"]
        assert [c["constraint"] for c in constraints["removed"]] == ["uq_name"]

    def test_changed_constraint_is_separate_from_add_remove(self):
        old = (
            "CREATE TABLE users (a INT, b INT, "
            "CONSTRAINT uq_ab UNIQUE (a));"
        )
        new = (
            "CREATE TABLE users (a INT, b INT, "
            "CONSTRAINT uq_ab UNIQUE (a, b));"
        )
        constraints = _changed_table(diff_ddls(old, new, "mysql"))["constraints"]
        assert constraints["added"] == []
        assert constraints["removed"] == []
        assert len(constraints["changed"]) == 1
        change = constraints["changed"][0]
        assert change["old"]["constraint"] == "uq_ab"
        assert change["old"]["definition"]["columns"] == ["a"]
        assert change["new"]["definition"]["columns"] == ["a", "b"]

    def test_check_constraint_change(self):
        old = "CREATE TABLE users (a INT, CONSTRAINT chk_a CHECK (a > 0));"
        new = "CREATE TABLE users (a INT, CONSTRAINT chk_a CHECK (a > 10));"
        constraints = _changed_table(diff_ddls(old, new, "mysql"))["constraints"]
        assert len(constraints["changed"]) == 1
        assert constraints["changed"][0]["old"]["category"] == "checks"

    def test_foreign_key_added(self):
        old = "CREATE TABLE orders (id INT, user_id INT);"
        new = (
            "CREATE TABLE orders (id INT, user_id INT, "
            "CONSTRAINT fk_user FOREIGN KEY (user_id) REFERENCES users (id));"
        )
        constraints = _changed_table(diff_ddls(old, new, "mysql"), "orders")[
            "constraints"
        ]
        assert [c["constraint"] for c in constraints["added"]] == ["fk_user"]
        assert constraints["added"][0]["category"] == "references"
