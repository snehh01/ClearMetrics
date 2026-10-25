
import io
import math
import os
import sqlite3

import pandas as pd


# =========================================================
# CUSTOM ERRORS
# =========================================================

class DataFileError(Exception):
    """Raised when a data file cannot be loaded."""


class CleaningError(Exception):
    """Raised when data cleaning or trend analysis fails."""


class QueryError(Exception):
    """Raised when an SQL query fails."""


# =========================================================
# HELPER FUNCTIONS
# =========================================================

def _number(value):
    """Convert a scalar value into a finite Python float or None."""
    if value is None or value is pd.NA or value is pd.NaT:
        return None

    try:
        if pd.isna(value):
            return None

        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _parse_dates(series):
    """Parse common date formats, including mixed and day-first dates."""
    values = series.astype("string").str.strip()

    try:
        dates = pd.to_datetime(
            values,
            errors="coerce",
            format="mixed",
        )
    except (TypeError, ValueError):
        dates = pd.to_datetime(values, errors="coerce")

    if dates.notna().sum() == 0:
        try:
            dates = pd.to_datetime(
                values,
                errors="coerce",
                dayfirst=True,
            )
        except (TypeError, ValueError):
            pass

    # Normalize timezone-aware dates so they can be resampled consistently.
    try:
        if getattr(dates.dt, "tz", None) is not None:
            dates = dates.dt.tz_localize(None)
    except (AttributeError, TypeError, ValueError):
        pass

    return dates


def _parse_numeric(series):
    """Convert numbers stored as text, including currency and accounting negatives."""
    values = series.astype("string").str.strip()

    negative = values.str.match(r"^\s*\(.*\)\s*$", na=False)

    values = values.str.replace(
        r"^\s*\((.*)\)\s*$",
        r"\1",
        regex=True,
    )

    # Remove currency symbols, thousands separators, spaces and percent signs.
    values = values.str.replace(
        r"[₹$£€,\s%]",
        "",
        regex=True,
    )

    values.loc[negative] = "-" + values.loc[negative]

    return pd.to_numeric(values, errors="coerce")


def _excel_safe_frame(df):
    """Prepare a DataFrame for Excel export."""
    result = df.copy()

    # Excel cannot store timezone-aware datetimes.
    for column in result.columns:
        if isinstance(result[column].dtype, pd.DatetimeTZDtype):
            result[column] = result[column].dt.tz_localize(None)

    return result


# =========================================================
# LOAD CSV OR EXCEL FILE
# Supports uploaded files and local paths.
# =========================================================

def load_file(file):
    """Load a CSV or Excel file into a pandas DataFrame."""
    if isinstance(file, (str, os.PathLike)):
        filename = os.fspath(file)
    else:
        filename = getattr(file, "filename", "") or ""

    extension = os.path.splitext(filename)[1].lower()

    try:
        if extension == ".csv":
            df = pd.read_csv(file)
        elif extension == ".xlsx":
            df = pd.read_excel(file, engine="openpyxl")
        elif extension == ".xls":
            df = pd.read_excel(file)
        else:
            raise DataFileError(
                "Unsupported file type. Upload a CSV or Excel file."
            )

        if df.empty:
            raise DataFileError("The uploaded file contains no rows.")

        if len(df.columns) == 0:
            raise DataFileError("The uploaded file contains no columns.")

        df.columns = [str(column).strip() for column in df.columns]

        if any(not column for column in df.columns):
            raise DataFileError("Column names cannot be empty.")

        if df.columns.duplicated().any():
            duplicates = df.columns[df.columns.duplicated()].tolist()
            raise DataFileError(
                f"Duplicate column names found: {duplicates}. "
                "Rename them and try again."
            )

        return df

    except DataFileError:
        raise
    except Exception as exc:
        raise DataFileError(f"Could not read the file: {exc}") from exc


# =========================================================
# DATA QUALITY PROFILE
# =========================================================

def profile(df):
    """Calculate data-quality metrics for the dataset."""
    missing_by_column = df.isna().sum()

    return {
        "rows": int(len(df)),
        "columns": int(len(df.columns)),
        "missing": int(df.isna().sum().sum()),
        "duplicates": int(df.duplicated().sum()),
        "missing_by_column": {
            str(column): int(count)
            for column, count in missing_by_column.items()
        },
        "column_types": {
            str(column): str(dtype)
            for column, dtype in df.dtypes.items()
        },
    }


# =========================================================
# DATA CLEANING
# =========================================================

def clean(
    df,
    num_strategy="median",
    cat_strategy="mode",
    drop_dupes=True,
):
    """
    Clean missing values and optionally remove duplicate rows.

    Numeric strategies: median, mean, zero, drop
    Categorical strategies: mode, unknown, drop

    Returns:
        cleaned_dataframe, cleaning_log
    """
    try:
        result = df.copy()
        log = []

        if drop_dupes:
            before_count = len(result)
            result = result.drop_duplicates()
            removed = before_count - len(result)

            log.append({
                "action": "Remove duplicates",
                "details": f"Removed {removed} duplicate row(s).",
                "count": int(removed),
            })

        for column in result.columns:
            missing_count = int(result[column].isna().sum())

            if missing_count == 0:
                continue

            is_numeric = pd.api.types.is_numeric_dtype(result[column])

            if is_numeric:
                if num_strategy == "median":
                    fill_value = result[column].median()
                elif num_strategy == "mean":
                    fill_value = result[column].mean()
                elif num_strategy == "zero":
                    fill_value = 0
                elif num_strategy == "drop":
                    result = result.dropna(subset=[column])
                    log.append({
                        "action": "Handle missing values",
                        "details": f"Dropped rows missing '{column}'.",
                        "count": missing_count,
                    })
                    continue
                else:
                    raise CleaningError(
                        f"Unknown numeric strategy: {num_strategy}"
                    )
            else:
                if cat_strategy == "mode":
                    modes = result[column].mode(dropna=True)
                    fill_value = modes.iloc[0] if not modes.empty else "Unknown"
                elif cat_strategy == "unknown":
                    fill_value = "Unknown"
                elif cat_strategy == "drop":
                    result = result.dropna(subset=[column])
                    log.append({
                        "action": "Handle missing values",
                        "details": f"Dropped rows missing '{column}'.",
                        "count": missing_count,
                    })
                    continue
                else:
                    raise CleaningError(
                        f"Unknown categorical strategy: {cat_strategy}"
                    )

            if pd.notna(fill_value):
                result[column] = result[column].fillna(fill_value)

            strategy = num_strategy if is_numeric else cat_strategy

            log.append({
                "action": "Handle missing values",
                "details": (
                    f"Handled missing values in '{column}' "
                    f"using {strategy}."
                ),
                "count": missing_count,
            })

        log.append({
            "action": "Cleaning complete",
            "details": (
                f"Dataset contains {len(result)} rows "
                f"and {len(result.columns)} columns."
            ),
            "count": int(len(result)),
        })

        return result, log

    except CleaningError:
        raise
    except Exception as exc:
        raise CleaningError(f"Could not clean the dataset: {exc}") from exc


# =========================================================
# NUMERIC INSIGHTS
# =========================================================

def summary(df):
    """Calculate total, average, minimum and maximum for numeric columns."""
    results = []
    numeric_df = df.select_dtypes(include="number")

    for column in numeric_df.columns:
        values = numeric_df[column].dropna()

        if values.empty:
            continue

        results.append({
            "column": str(column),
            "total": _number(values.sum()),
            "average": _number(values.mean()),
            "min": _number(values.min()),
            "max": _number(values.max()),
        })

    return results


# =========================================================
# TREND EXPLORER
# =========================================================

def trend(df, date_col, value_col, freq="M"):
    """
    Aggregate numeric values by date.

    Supported frequency values:
        D / daily
        W / weekly
        M / monthly / ME

    Returns a JSON-friendly dictionary containing aggregated data.
    """
    if date_col not in df.columns:
        raise CleaningError(f"Date column '{date_col}' was not found.")

    if value_col not in df.columns:
        raise CleaningError(f"Value column '{value_col}' was not found.")

    if date_col == value_col:
        raise CleaningError("Choose different date and numeric columns.")

    frequency_aliases = {
        "D": "D",
        "DAILY": "D",
        "W": "W",
        "WEEKLY": "W",
        "M": "ME",
        "ME": "ME",
        "MONTHLY": "ME",
    }
    requested_freq = str(freq or "M").strip().upper()
    resample_freq = frequency_aliases.get(requested_freq)

    if resample_freq is None:
        raise CleaningError(
            "Choose daily, weekly, or monthly frequency."
        )

    try:
        dates = _parse_dates(df[date_col])
        values = _parse_numeric(df[value_col])

        temp = pd.DataFrame({
            "date": dates,
            "value": values,
        }).dropna(subset=["date", "value"])

        temp = temp[temp["value"].map(math.isfinite)]

        if temp.empty:
            date_examples = (
                df[date_col].dropna().astype(str).head(5).tolist()
            )
            value_examples = (
                df[value_col].dropna().astype(str).head(5).tolist()
            )

            raise CleaningError(
                "No valid date and numeric pairs were found. "
                f"Date examples: {date_examples}. "
                f"Value examples: {value_examples}. "
                "Choose a column containing dates and another containing numbers."
            )

        temp = temp.set_index("date").sort_index()

        grouped = temp["value"].resample(resample_freq).sum().to_frame("total")
        grouped["change_pct"] = grouped["total"].pct_change() * 100
        grouped["moving_average"] = grouped["total"].rolling(
            3, min_periods=1
        ).mean()

        grouped = grouped.reset_index()
        grouped["period"] = grouped["date"].dt.strftime("%Y-%m-%d")

        data = []
        for _, row in grouped.iterrows():
            data.append({
                "period": row["period"],
                "total": _number(row["total"]),
                "change_pct": _number(row["change_pct"]),
                "moving_average": _number(row["moving_average"]),
            })

        return {
            "date_col": str(date_col),
            "value_col": str(value_col),
            "freq": requested_freq,
            "data": data,
        }

    except CleaningError:
        raise
    except Exception as exc:
        raise CleaningError(f"Could not generate trend: {exc}") from exc


# =========================================================
# SQL QUERY LAB
# =========================================================

def run_sql(df, query):
    """
    Run a read-only SQL query against the dataset table named `data`.

    Returns a pandas DataFrame because app.py expects DataFrame methods
    such as .columns, .head() and len().
    """
    if not query or not query.strip():
        raise QueryError("Enter an SQL query.")

    cleaned_query = query.strip()

    # Permit one statement only; a trailing semicolon is fine.
    if ";" in cleaned_query.rstrip(";"):
        raise QueryError("Run one SQL statement at a time.")

    first_word = cleaned_query.split(None, 1)[0].lower()

    if first_word not in {"select", "with"}:
        raise QueryError(
            "Only SELECT queries and read-only CTEs are allowed."
        )

    connection = sqlite3.connect(":memory:")

    # Block database changes and other potentially unsafe operations.
    denied_actions = {
        sqlite3.SQLITE_INSERT,
        sqlite3.SQLITE_UPDATE,
        sqlite3.SQLITE_DELETE,
        sqlite3.SQLITE_CREATE_INDEX,
        sqlite3.SQLITE_CREATE_TABLE,
        sqlite3.SQLITE_CREATE_TEMP_INDEX,
        sqlite3.SQLITE_CREATE_TEMP_TABLE,
        sqlite3.SQLITE_CREATE_TEMP_TRIGGER,
        sqlite3.SQLITE_CREATE_TEMP_VIEW,
        sqlite3.SQLITE_CREATE_TRIGGER,
        sqlite3.SQLITE_CREATE_VIEW,
        sqlite3.SQLITE_DROP_INDEX,
        sqlite3.SQLITE_DROP_TABLE,
        sqlite3.SQLITE_DROP_TEMP_INDEX,
        sqlite3.SQLITE_DROP_TEMP_TABLE,
        sqlite3.SQLITE_DROP_TEMP_TRIGGER,
        sqlite3.SQLITE_DROP_TEMP_VIEW,
        sqlite3.SQLITE_DROP_TRIGGER,
        sqlite3.SQLITE_DROP_VIEW,
        sqlite3.SQLITE_ALTER_TABLE,
        sqlite3.SQLITE_ATTACH,
        sqlite3.SQLITE_DETACH,
        sqlite3.SQLITE_PRAGMA,
    }

    def read_only_authorizer(action, _arg1, _arg2, _database, _source):
        if action in denied_actions:
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    try:
        df.to_sql(
            "data",
            connection,
            index=False,
            if_exists="replace",
        )

        connection.set_authorizer(read_only_authorizer)

        result = pd.read_sql_query(cleaned_query, connection)

        # app.py serializes the DataFrame to JSON.
        return result

    except QueryError:
        raise
    except Exception as exc:
        raise QueryError(f"SQL query failed: {exc}") from exc
    finally:
        connection.close()


# =========================================================
# CSV EXPORT
# =========================================================

def export_csv(df):
    """Return a file-like object suitable for Flask send_file()."""
    try:
        output = io.BytesIO(df.to_csv(index=False).encode("utf-8-sig"))
        output.seek(0)
        return output
    except Exception as exc:
        raise DataFileError(f"Could not export CSV: {exc}") from exc


# =========================================================
# EXCEL EXPORT
# =========================================================

def export_excel(df, summary_df=None, trend_df=None):
    """
    Build a multi-sheet Excel workbook.

    The optional arguments match app.py's export route:
        export_excel(dataframe, summary_dataframe, trend_dataframe)
    """
    try:
        output = io.BytesIO()

        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            _excel_safe_frame(df).to_excel(
                writer,
                index=False,
                sheet_name="Cleaned Data",
            )

            if summary_df is None:
                summary_df = pd.DataFrame(summary(df))

            _excel_safe_frame(summary_df).to_excel(
                writer,
                index=False,
                sheet_name="Summary",
            )

            if trend_df is None:
                trend_df = pd.DataFrame()

            _excel_safe_frame(trend_df).to_excel(
                writer,
                index=False,
                sheet_name="Trend",
            )

        output.seek(0)
        return output

    except Exception as exc:
        raise DataFileError(f"Could not export Excel: {exc}") from exc
