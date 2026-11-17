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


def diff_ddls(old_ddl: str, new_ddl: str, dialect: str) -> Dict:
    """Compare two DDL scripts and return a difference report.

    Args:
        old_ddl: DDL text of the previous schema version.
        new_ddl: DDL text of the new schema version.
        dialect: target dialect, one of ``SUPPORTED_DIFF_DIALECTS``.

    Returns:
        A dict with keys ``dialect``, ``tables`` and ``statements``.
    """
    raise NotImplementedError
