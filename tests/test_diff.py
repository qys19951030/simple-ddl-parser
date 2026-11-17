"""Tests for simple_ddl_parser.diff (DDL vs DDL comparison)."""

from simple_ddl_parser.diff import diff_ddls


def test_diff_module_importable():
    assert callable(diff_ddls)
