"""Data-quality, profiling, validation, correlation, and filtering helpers.

These functions are deliberately independent of Flask so they can be tested
and reused by other Python applications.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def data_quality_report(df: pd.DataFrame) -> dict[str, Any]:
    """Return high-level quality metrics and column-level missingness."""
    missing = df.isna().sum()
    total_cells = int(df.shape[0] * df.shape[1])
    numeric_columns = df.select_dtypes(include="number").columns.tolist()
    categorical_columns = [c for c in df.columns if c not in numeric_columns]
    return {
        "rows": int(len(df)),
        "columns": int(len(df.columns)),
        "total_cells": total_cells,
        "missing_cells": int(missing.sum()),
        "missing_percent": round(float(missing.sum() / total_cells * 100), 2) if total_cells else 0.0,
        "duplicate_rows": int(df.duplicated().sum()),
        "numeric_columns": int(len(numeric_columns)),
        "categorical_columns": int(len(categorical_columns)),
        "columns_with_missing": int((missing > 0).sum()),
        "column_missing": [
            {
                "column": str(column),
                "missing": int(count),
                "missing_percent": round(float(count / len(df) * 100), 2) if len(df) else 0.0,
                "unique_values": int(df[column].nunique(dropna=True)),
                "dtype": str(df[column].dtype),
            }
            for column, count in missing.items()
        ],
    }


def column_profile(df: pd.DataFrame) -> list[dict[str, Any]]:
    """Describe each field with type, completeness, uniqueness, and examples."""
    profiles = []
    for column in df.columns:
        series = df[column]
        non_null = series.dropna()
        samples = [str(value)[:100] for value in non_null.head(3).tolist()]
        profiles.append({
            "column": str(column),
            "dtype": str(series.dtype),
            "rows": int(len(series)),
            "missing": int(series.isna().sum()),
            "missing_percent": round(float(series.isna().mean() * 100), 2) if len(series) else 0.0,
            "unique": int(series.nunique(dropna=True)),
            "unique_percent": round(float(series.nunique(dropna=True) / len(series) * 100), 2) if len(series) else 0.0,
            "sample_values": samples,
            "is_numeric": bool(pd.api.types.is_numeric_dtype(series)),
        })
    return profiles


def validate_dataset(df: pd.DataFrame, rules: dict[str, Any] | None = None) -> dict[str, Any]:
    """Validate common column and row rules.

    Supported rule keys: required_columns, no_missing, unique, numeric.
    Each value is a list of column names.
    """
    rules = rules or {}
    if not isinstance(rules, dict):
        raise TypeError("Validation rules must be an object.")

    checks: list[dict[str, Any]] = []
    existing = set(df.columns)
    required = rules.get("required_columns", [])
    if not isinstance(required, list):
        raise TypeError("required_columns must be a list of column names.")
    missing_required = [str(c) for c in required if c not in existing]
    checks.append({
        "rule": "required_columns",
        "passed": not missing_required,
        "message": "All required columns are present." if not missing_required else "Required columns are missing.",
        "details": missing_required,
    })

    for rule_name, predicate, message_pass, message_fail in (
        ("no_missing", lambda s: int(s.isna().sum()) == 0, "No missing values found.", "Missing values found."),
        ("unique", lambda s: bool(s.is_unique), "Values are unique.", "Duplicate values found."),
        ("numeric", lambda s: bool(pd.api.types.is_numeric_dtype(s)), "Column is numeric.", "Column is not numeric."),
    ):
        columns = rules.get(rule_name, [])
        if not isinstance(columns, list):
            raise TypeError(f"{rule_name} must be a list of column names.")
        for name in columns:
            if name not in existing:
                checks.append({"rule": rule_name, "column": str(name), "passed": False, "message": "Column does not exist."})
                continue
            passed = bool(predicate(df[name]))
            checks.append({
                "rule": rule_name,
                "column": str(name),
                "passed": passed,
                "message": message_pass if passed else message_fail,
                "details": int(df[name].isna().sum()) if rule_name == "no_missing" else None,
            })

    passed_count = sum(bool(check["passed"]) for check in checks)
    return {
        "passed": passed_count == len(checks),
        "checks_passed": int(passed_count),
        "checks_total": int(len(checks)),
        "checks": checks,
    }


def correlation_report(df: pd.DataFrame) -> dict[str, Any]:
    """Return numeric correlations and IQR outlier counts per numeric field."""
    numeric = df.select_dtypes(include="number")
    corr = numeric.corr(numeric_only=True).replace([np.inf, -np.inf], np.nan)
    correlations = {
        str(row): {
            str(column): (None if pd.isna(value) else round(float(value), 4))
            for column, value in values.items()
        }
        for row, values in corr.iterrows()
    }
    outliers = []
    for column in numeric.columns:
        series = numeric[column].dropna()
        if series.empty:
            count = 0
            lower = upper = None
        else:
            q1, q3 = series.quantile([0.25, 0.75])
            iqr = q3 - q1
            lower = float(q1 - 1.5 * iqr)
            upper = float(q3 + 1.5 * iqr)
            count = int(((series < lower) | (series > upper)).sum())
        outliers.append({
            "column": str(column),
            "outlier_count": count,
            "lower_bound": lower,
            "upper_bound": upper,
        })
    return {
        "numeric_columns": [str(c) for c in numeric.columns],
        "correlation_matrix": correlations,
        "outliers": outliers,
    }


def filter_dataset(
    df: pd.DataFrame,
    column: str,
    operation: str,
    value: Any = None,
    second_value: Any = None,
) -> pd.DataFrame:
    """Return a filtered copy using a small explicit set of operations."""
    if column not in df.columns:
        raise KeyError(f"Unknown column: {column}")
    series = df[column]
    op = str(operation or "").strip().lower()

    if op in {"is_null", "is null", "empty"}:
        mask = series.isna()
    elif op in {"not_null", "not null", "not_empty"}:
        mask = series.notna()
    elif op in {"contains", "not_contains", "startswith", "endswith"}:
        text = series.astype("string")
        term = str(value if value is not None else "")
        if op == "contains":
            mask = text.str.contains(term, case=False, regex=False, na=False)
        elif op == "not_contains":
            mask = ~text.str.contains(term, case=False, regex=False, na=False)
        elif op == "startswith":
            mask = text.str.startswith(term, na=False)
        else:
            mask = text.str.endswith(term, na=False)
    elif op in {"in", "not_in"}:
        choices = value if isinstance(value, list) else [value]
        mask = series.isin(choices)
        if op == "not_in":
            mask = ~mask
    elif op == "between":
        low, high = value, second_value
        try:
            low, high = float(low), float(high)
            comparable = pd.to_numeric(series, errors="coerce")
        except (TypeError, ValueError):
            comparable = series
        mask = comparable.between(low, high, inclusive="both")
    elif op in {"eq", "ne", "gt", "gte", "lt", "lte"}:
        compare_value = value
        if pd.api.types.is_numeric_dtype(series):
            try:
                compare_value = float(value)
            except (TypeError, ValueError):
                raise ValueError(f"Filter value for '{column}' must be numeric.")
        elif pd.api.types.is_datetime64_any_dtype(series):
            compare_value = pd.to_datetime(value, errors="coerce")
        try:
            if op == "eq": mask = series == compare_value
            elif op == "ne": mask = series != compare_value
            elif op == "gt": mask = series > compare_value
            elif op == "gte": mask = series >= compare_value
            elif op == "lt": mask = series < compare_value
            else: mask = series <= compare_value
        except TypeError as exc:
            raise ValueError(f"Filter operation '{op}' is not valid for '{column}'.") from exc
    else:
        raise ValueError(f"Unsupported filter operation: {operation}")

    return df.loc[mask.fillna(False)].copy()
