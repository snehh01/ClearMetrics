
import io
import os
import secrets
import threading
from datetime import date, datetime

import numpy as np
import pandas as pd

from flask import (
    Flask,
    request,
    jsonify as flask_jsonify,
    render_template,
    session,
    send_file,
)

from werkzeug.utils import secure_filename

from engine import (
    load_file,
    profile,
    clean,
    summary,
    trend,
    run_sql,
    export_csv,
    export_excel,
    DataFileError,
    CleaningError,
    QueryError,
)

from quality_tools import (
    data_quality_report,
    column_profile,
    validate_dataset,
    correlation_report,
    filter_dataset,
)


# =========================================================
# APP CONFIGURATION
# =========================================================

app = Flask(__name__)
app.secret_key = os.environ.get("CLEAR_METRICS_SECRET_KEY", secrets.token_hex(32))
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024

STORE = {}
STORE_LOCK = threading.Lock()


# =========================================================
# GENERAL HELPERS
# =========================================================

def json_safe(value):
    """Convert pandas and NumPy values into JSON-safe values."""

    if isinstance(value, dict):
        return {
            str(key): json_safe(item)
            for key, item in value.items()
        }

    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]

    if isinstance(value, np.integer):
        return int(value)

    if isinstance(value, np.floating):
        return None if not np.isfinite(value) else float(value)

    if isinstance(value, np.bool_):
        return bool(value)

    if isinstance(value, (pd.Timestamp, datetime, date)):
        return value.isoformat()

    if value is pd.NA or value is pd.NaT:
        return None

    if isinstance(value, float) and not np.isfinite(value):
        return None

    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass

    return value


def jsonify(data, status=200):
    return flask_jsonify(json_safe(data)), status


def error_response(message, status=400):
    return jsonify(
        {
            "ok": False,
            "error": str(message),
        },
        status,
    )


def state():
    """Return the dataset state belonging to the current session."""

    key = session.get("dataset_key")

    with STORE_LOCK:
        if not key or key not in STORE:
            key = secrets.token_urlsafe(16)
            session["dataset_key"] = key

            STORE[key] = {
                "raw": None,
                "cleaned": None,
                "filename": None,
                "profile": None,
                "summary": None,
                "trend": None,
                "clean_log": [],
                "history": [],
                "active_filters": [],
            }

        return STORE[key]


def current_df(cleaned=True):
    """Get the current raw or cleaned DataFrame."""

    data = state()

    df = data["cleaned"] if cleaned else data["raw"]

    if df is None:
        raise DataFileError(
            "Upload a dataset or load the sample dataset first."
        )

    return df


def require_dataset():
    data = state()

    if data["cleaned"] is None:
        raise DataFileError(
            "Upload a dataset or load the sample dataset first."
        )

    return data


def preview_records(df, limit=10):
    """Return a small preview for the frontend."""

    preview = df.head(limit).copy()

    preview = preview.astype(object).where(
        pd.notna(preview),
        None,
    )

    return {
        "columns": [str(column) for column in preview.columns],
        "rows": preview.values.tolist(),
    }


def profile_dict(df):
    """Return dataset statistics with consistent field names."""

    result = profile(df)

    if not isinstance(result, dict):
        result = {}

    result = dict(result)

    # Calculate these directly to ensure consistent values.
    result["rows"] = int(df.shape[0])
    result["columns"] = int(df.shape[1])
    result["missing"] = int(df.isna().sum().sum())
    result["duplicates"] = int(df.duplicated().sum())

    return json_safe(result)


def load_into_state(df, filename):
    """Store a newly loaded dataset and return its metadata."""

    if not isinstance(df, pd.DataFrame):
        raise DataFileError("The loaded file could not be read as a table.")

    if df.empty:
        raise DataFileError("The dataset contains no rows.")

    data = state()

    data["raw"] = df.copy()
    data["cleaned"] = df.copy()
    data["filename"] = filename
    data["profile"] = profile_dict(df)
    data["summary"] = None
    data["trend"] = None
    data["clean_log"] = []
    data["history"] = []
    data["active_filters"] = []

    return {
        "ok": True,
        "filename": filename,
        "profile": data["profile"],
        "preview": preview_records(df),
        "columns": [str(column) for column in df.columns],
        "rows": int(len(df)),
    }


# =========================================================
# DASHBOARD HELPERS
# =========================================================

def find_date_column(df):
    """Detect a usable date column."""

    for column in df.columns:
        name = str(column).lower()

        if any(
            word in name
            for word in ("date", "time", "day")
        ):
            parsed = pd.to_datetime(
                df[column],
                errors="coerce",
            )

            if parsed.notna().any():
                return column

    for column in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[column]):
            return column

    return None


def find_numeric_column(df):
    """Return the first numeric column, if one exists."""

    numeric_columns = df.select_dtypes(
        include="number"
    ).columns.tolist()

    return numeric_columns[0] if numeric_columns else None


def build_dashboard(df):
    """Build dashboard metrics and chart data from the full dataset."""

    numeric_columns = df.select_dtypes(
        include="number"
    ).columns.tolist()

    date_column = find_date_column(df)

    # -----------------------------------------------------
    # KPI statistics
    # -----------------------------------------------------

    kpis = {
        "rows": int(len(df)),
        "columns": int(len(df.columns)),
        "missing": int(df.isna().sum().sum()),
        "duplicates": int(df.duplicated().sum()),
    }

    # -----------------------------------------------------
    # Time-series chart
    # -----------------------------------------------------

    time_series = {
        "available": False,
        "title": "Values over time",
        "date_column": (
            str(date_column)
            if date_column is not None
            else None
        ),
        "value_column": None,
        "labels": [],
        "values": [],
    }

    if date_column is not None and numeric_columns:
        preferred_names = (
            "sales",
            "revenue",
            "amount",
            "profit",
            "total",
        )

        value_column = next(
            (
                column
                for column in numeric_columns
                if any(
                    name in str(column).lower()
                    for name in preferred_names
                )
            ),
            numeric_columns[0],
        )

        work = pd.DataFrame(
            {
                "_date": pd.to_datetime(
                    df[date_column],
                    errors="coerce",
                ),
                "_value": pd.to_numeric(
                    df[value_column],
                    errors="coerce",
                ),
            }
        ).dropna()

        if not work.empty:
            work["_date"] = work["_date"].dt.strftime(
                "%Y-%m-%d"
            )

            grouped = (
                work.groupby("_date")["_value"]
                .sum()
                .sort_index()
            )

            time_series.update(
                {
                    "available": True,
                    "title": f"{value_column} over time",
                    "value_column": str(value_column),
                    "labels": grouped.index.tolist(),
                    "values": grouped.round(2).tolist(),
                }
            )

    # -----------------------------------------------------
    # Category breakdown chart
    # -----------------------------------------------------

    category_chart = {
        "available": False,
        "title": "Records by category",
        "category_column": None,
        "value_column": None,
        "labels": [],
        "values": [],
    }

    category_columns = []

    for column in df.columns:
        is_numeric = pd.api.types.is_numeric_dtype(df[column])
        is_datetime = pd.api.types.is_datetime64_any_dtype(
            df[column]
        )

        if is_numeric or is_datetime:
            continue

        unique_count = df[column].nunique(dropna=True)

        if 1 < unique_count <= 20:
            category_columns.append(column)

    if category_columns:
        preferred_categories = (
            "category",
            "region",
            "product",
            "type",
            "status",
            "department",
        )

        category_column = next(
            (
                column
                for column in category_columns
                if any(
                    name in str(column).lower()
                    for name in preferred_categories
                )
            ),
            category_columns[0],
        )

        value_column = find_numeric_column(df)

        if value_column is not None:
            work = df[
                [category_column, value_column]
            ].copy()

            work[value_column] = pd.to_numeric(
                work[value_column],
                errors="coerce",
            )

            grouped = (
                work.dropna(subset=[category_column])
                .groupby(category_column)[value_column]
                .sum()
                .sort_values(ascending=False)
                .head(10)
            )

            chart_title = (
                f"{value_column} by {category_column}"
            )

        else:
            grouped = (
                df[category_column]
                .dropna()
                .astype(str)
                .value_counts()
                .head(10)
            )

            chart_title = (
                f"Records by {category_column}"
            )

        if not grouped.empty:
            category_chart.update(
                {
                    "available": True,
                    "title": chart_title,
                    "category_column": str(category_column),
                    "value_column": (
                        str(value_column)
                        if value_column is not None
                        else None
                    ),
                    "labels": [
                        str(value)
                        for value in grouped.index.tolist()
                    ],
                    "values": [
                        float(value)
                        for value in grouped.tolist()
                    ],
                }
            )

    # -----------------------------------------------------
    # Numeric distribution histogram
    # -----------------------------------------------------

    distribution = {
        "available": False,
        "title": "Numeric distribution",
        "column": None,
        "labels": [],
        "values": [],
    }

    if numeric_columns:
        preferred_numeric_names = (
            "sales",
            "revenue",
            "amount",
            "profit",
            "price",
            "quantity",
        )

        distribution_column = next(
            (
                column
                for column in numeric_columns
                if any(
                    name in str(column).lower()
                    for name in preferred_numeric_names
                )
            ),
            numeric_columns[0],
        )

        numeric_values = pd.to_numeric(
            df[distribution_column],
            errors="coerce",
        ).dropna()

        if not numeric_values.empty:
            # FIXED: complete np.histogram call.
            bin_count = min(
                10,
                max(1, int(numeric_values.nunique())),
            )

            counts, edges = np.histogram(
                numeric_values.to_numpy(),
                bins=bin_count,
            )

            distribution.update(
                {
                    "available": True,
                    "title": (
                        f"Distribution of {distribution_column}"
                    ),
                    "column": str(distribution_column),
                    "labels": [
                        (
                            f"{edges[index]:.2f}"
                            f"–{edges[index + 1]:.2f}"
                        )
                        for index in range(len(edges) - 1)
                    ],
                    "values": counts.tolist(),
                }
            )

    # -----------------------------------------------------
    # Numeric relationship scatter plot (when applicable)
    # -----------------------------------------------------

    relationship_chart = {
        "available": False,
        "title": "Numeric relationship",
        "x_column": None,
        "y_column": None,
        "points": [],
    }

    if len(numeric_columns) >= 2:
        relationship_pair = None
        correlations = df[numeric_columns].corr(numeric_only=True)
        best_strength = -1.0
        for index, left in enumerate(numeric_columns):
            for right in numeric_columns[index + 1:]:
                corr_value = correlations.loc[left, right]
                if pd.notna(corr_value) and abs(float(corr_value)) > best_strength:
                    best_strength = abs(float(corr_value))
                    relationship_pair = (left, right)

        if relationship_pair:
            x_column, y_column = relationship_pair
            points_df = df[[x_column, y_column]].apply(
                pd.to_numeric, errors="coerce"
            ).dropna().head(500)
            if len(points_df) >= 2:
                relationship_chart = {
                    "available": True,
                    "title": f"{y_column} vs {x_column}",
                    "x_column": str(x_column),
                    "y_column": str(y_column),
                    "points": [
                        {"x": float(row[x_column]), "y": float(row[y_column])}
                        for _, row in points_df.iterrows()
                    ],
                }

    # -----------------------------------------------------
    # Data-grounded insights and statistical summaries
    # -----------------------------------------------------

    insights = []
    numeric_summary = []
    categorical_summary = []

    if len(df.columns):
        numeric_count = len(numeric_columns)
        categorical_count = sum(
            1 for column in df.columns
            if not pd.api.types.is_numeric_dtype(df[column])
            and not pd.api.types.is_datetime64_any_dtype(df[column])
        )
        insights.append(
            f"This dataset contains {len(df):,} records and {len(df.columns):,} fields. "
            f"It has {numeric_count} numeric field(s) and {categorical_count} categorical or text field(s)."
        )

    if kpis["missing"] > 0:
        missing_by_column = df.isna().sum().sort_values(ascending=False)
        affected = [
            f"{column} ({int(count):,})"
            for column, count in missing_by_column.items() if count > 0
        ]
        insights.append(
            f"{kpis['missing']:,} missing values were found across the dataset. "
            f"Most affected fields: {', '.join(affected[:3])}."
        )
    else:
        insights.append("No missing values were found in the dataset.")

    if kpis["duplicates"] > 0:
        insights.append(f"{kpis['duplicates']:,} duplicate rows were found.")
    else:
        insights.append("No duplicate rows were found.")

    for column in numeric_columns:
        values = pd.to_numeric(df[column], errors="coerce").dropna()
        if values.empty:
            continue
        numeric_summary.append({
            "column": str(column),
            "count": int(values.count()),
            "mean": round(float(values.mean()), 4),
            "median": round(float(values.median()), 4),
            "min": round(float(values.min()), 4),
            "max": round(float(values.max()), 4),
            "std": round(float(values.std(ddof=1)), 4) if len(values) > 1 else 0.0,
            "missing": int(df[column].isna().sum()),
        })
        if values.nunique() > 1:
            low, high = values.min(), values.max()
            if low < 0 < high:
                insights.append(
                    f"{column} includes both negative and positive values (from {low:g} to {high:g}); "
                    "check whether the sign has business meaning."
                )
            if values.nunique() <= 10:
                insights.append(
                    f"{column} has {values.nunique()} distinct numeric values, so it may behave more like a category than a continuous measure."
                )

    for column in df.columns:
        if column in numeric_columns or pd.api.types.is_datetime64_any_dtype(df[column]):
            continue
        values = df[column].dropna().astype(str)
        unique_count = int(values.nunique())
        if unique_count == 0:
            continue
        counts = values.value_counts().head(5)
        categorical_summary.append({
            "column": str(column),
            "count": int(values.size),
            "unique": unique_count,
            "top_values": [
                {"value": str(value), "count": int(count)}
                for value, count in counts.items()
            ],
        })
        if 1 < unique_count <= 20:
            top_value = str(counts.index[0])
            share = float(counts.iloc[0]) / len(values) * 100
            if share >= 50:
                insights.append(
                    f"{column} is concentrated in '{top_value}', which represents {share:.1f}% of non-missing values."
                )

    if date_column is not None:
        parsed_dates = pd.to_datetime(df[date_column], errors="coerce").dropna()
        if not parsed_dates.empty:
            insights.append(
                f"{date_column} spans {parsed_dates.min().date().isoformat()} to {parsed_dates.max().date().isoformat()} "
                f"({parsed_dates.nunique():,} distinct date/time values)."
            )
    else:
        insights.append("No usable date column was detected, so a time-series chart is not available.")

    if numeric_columns and len(numeric_columns) >= 2:
        correlations = df[numeric_columns].corr(numeric_only=True)
        pairs = []
        for i, left in enumerate(numeric_columns):
            for right in numeric_columns[i + 1:]:
                value = correlations.loc[left, right]
                if pd.notna(value):
                    pairs.append((abs(float(value)), float(value), str(left), str(right)))
        if pairs:
            strength, correlation, left, right = max(pairs)
            if strength >= 0.7:
                direction = "positive" if correlation > 0 else "negative"
                insights.append(
                    f"{left} and {right} show a strong {direction} linear relationship in this dataset (correlation {correlation:.2f}). "
                    "Correlation does not establish causation."
                )

    # Describe the likely subject using column names only; avoid claiming facts
    # that the actual dataset does not support.
    column_names_lower = [str(column).lower() for column in df.columns]
    topic_rules = [
        (("sales", "revenue", "profit", "order", "invoice", "quantity", "price"), "sales, orders, or business performance"),
        (("customer", "client", "churn", "retention", "segment"), "customer information or customer behavior"),
        (("employee", "salary", "department", "hire", "job", "performance"), "employee or workforce information"),
        (("product", "sku", "inventory", "stock", "warehouse"), "products or inventory"),
        (("student", "grade", "score", "exam", "school", "course"), "education or student performance"),
        (("sepal", "petal", "species"), "flower measurements and species classification"),
        (("latitude", "longitude", "country", "city", "region", "address"), "geographic or location-based records"),
        (("temperature", "humidity", "rainfall", "weather", "wind"), "weather or environmental measurements"),
        (("date", "timestamp", "time", "hour", "minute"), "records with a time or date component"),
    ]
    detected_topic = next(
        (description for keywords, description in topic_rules
         if any(any(keyword in name for keyword in keywords) for name in column_names_lower)),
        None,
    )
    sample_fields = ", ".join(str(column) for column in df.columns[:5])
    if detected_topic:
        dataset_description = (
            f"This appears to be a dataset about {detected_topic}, based on its column names. "
            f"It contains {len(df):,} records and {len(df.columns):,} fields. "
            f"Example fields: {sample_fields}."
        )
    else:
        dataset_description = (
            f"This is a structured table with {len(df):,} records and {len(df.columns):,} fields. "
            f"Based on the available column names, the fields include {sample_fields}. "
            "The summary below describes patterns calculated directly from the loaded values."
        )

    return {
        "ok": True,
        "filename": state().get("filename"),
        "kpis": kpis,
        "profile": profile_dict(df),
        "time_series": time_series,
        "category_chart": category_chart,
        "distribution": distribution,
        "relationship_chart": relationship_chart,
        "dataset_description": dataset_description,
        "statistical_summary": {
            "numeric": numeric_summary,
            "categorical": categorical_summary,
        },
        "insights": insights,
        "columns": [
            str(column) for column in df.columns
        ],
    }


# =========================================================
# MAIN PAGE
# =========================================================

@app.route("/")
def index():
    return render_template("index.html")


# =========================================================
# UPLOAD DATASET
# =========================================================

@app.route("/api/upload", methods=["POST"])
def upload():
    try:
        uploaded = request.files.get("file")

        if uploaded is None or not uploaded.filename:
            return error_response(
                "Please choose a file before uploading."
            )

        filename = secure_filename(uploaded.filename)

        allowed_extensions = (
            ".csv",
            ".xlsx",
            ".xls",
        )

        if not filename.lower().endswith(allowed_extensions):
            return error_response(
                "Unsupported file type. Upload a CSV or Excel file."
            )

        df = load_file(uploaded)

        if df is None or df.empty:
            return error_response(
                "The uploaded file contains no data."
            )

        return jsonify(
            load_into_state(df, filename)
        )

    except (DataFileError, ValueError) as exc:
        return error_response(exc)

    except Exception as exc:
        app.logger.exception("Dataset upload failed")

        return error_response(
            f"Could not upload the file: {exc}",
            500,
        )


# =========================================================
# LOAD SAMPLE DATASET
# =========================================================

@app.route("/api/sample", methods=["POST"])
def sample():
    try:
        sample_path = os.path.join(
            app.root_path,
            "sample_data.csv",
        )

        if not os.path.exists(sample_path):
            return error_response(
                "sample_data.csv was not found in the project folder.",
                404,
            )

        df = load_file(sample_path)

        if df is None or df.empty:
            return error_response(
                "The sample dataset contains no data."
            )

        return jsonify(
            load_into_state(df, "sample_data.csv")
        )

    except (DataFileError, ValueError) as exc:
        return error_response(exc)

    except Exception as exc:
        app.logger.exception("Sample dataset loading failed")

        return error_response(
            f"Could not load sample data: {exc}",
            500,
        )


# =========================================================
# DASHBOARD
# =========================================================

@app.route("/api/dashboard", methods=["GET"])
def dashboard():
    try:
        df = current_df()
        result = build_dashboard(df)
        result["quality"] = data_quality_report(df)
        result["column_profile"] = column_profile(df)

        return jsonify(result)

    except DataFileError as exc:
        return error_response(exc)

    except Exception as exc:
        app.logger.exception("Dashboard generation failed")

        return error_response(
            f"Could not build dashboard: {exc}",
            500,
        )


# =========================================================
# CLEAN DATASET
# =========================================================

@app.route("/api/clean", methods=["POST"])
def clean_data():
    try:
        data = state()

        if data["raw"] is None:
            raise DataFileError(
                "Upload a dataset or load the sample dataset first."
            )

        payload = request.get_json(silent=True) or {}

        num_strategy = payload.get(
            "num_strategy",
            "mean",
        )

        cat_strategy = payload.get(
            "cat_strategy",
            "mode",
        )

        drop_dupes = bool(
            payload.get("drop_dupes", True)
        )
        if bool(payload.get("remove_missing", False)):
            num_strategy = "drop"
            cat_strategy = "drop"

        before_df = data["cleaned"].copy()
        before_profile = profile_dict(before_df)

        data["history"].append({
            "dataframe": before_df.copy(),
            "log": list(data.get("clean_log", [])),
            "created_at": datetime.now().isoformat(timespec="seconds"),
        })
        data["history"] = data["history"][-20:]

        # Call the existing engine cleaning function.
        result = clean(
            before_df.copy(),
            num_strategy,
            cat_strategy,
            drop_dupes,
        )

        log = []

        # Support common return formats without evaluating a
        # DataFrame as a Boolean expression.
        if isinstance(result, pd.DataFrame):
            cleaned_df = result

        elif isinstance(result, dict):
            cleaned_df = None

            for key in ("df", "dataframe", "cleaned"):
                candidate = result.get(key)

                if isinstance(candidate, pd.DataFrame):
                    cleaned_df = candidate
                    break

            log = result.get("log", [])

        elif isinstance(result, tuple):
            cleaned_df = None

            if len(result) >= 1 and isinstance(
                result[0], pd.DataFrame
            ):
                cleaned_df = result[0]

            if len(result) >= 2:
                log = result[1]

        else:
            cleaned_df = getattr(
                result,
                "df",
                None,
            )

            log = getattr(
                result,
                "log",
                [],
            )

        if not isinstance(cleaned_df, pd.DataFrame):
            raise CleaningError(
                "The clean() function did not return a DataFrame. "
                "Check the return format of clean() in engine.py."
            )

        # Save the cleaned result.
        data["cleaned"] = cleaned_df.copy()
        data["profile"] = profile_dict(cleaned_df)
        data["summary"] = None
        data["trend"] = None
        data["clean_log"] = log

        return jsonify(
            {
                "ok": True,
                "before": before_profile,
                "after": data["profile"],
                "profile": data["profile"],
                "log": log,
                "preview": preview_records(cleaned_df),
                "columns": [
                    str(column)
                    for column in cleaned_df.columns
                ],
                "rows": int(len(cleaned_df)),
            }
        )

    except (DataFileError, CleaningError, ValueError) as exc:
        return error_response(exc)

    except Exception as exc:
        app.logger.exception("Dataset cleaning failed")

        return error_response(
            f"Could not clean the dataset: {exc}",
            500,
        )


# =========================================================
# SUMMARY AND INSIGHTS
# =========================================================

@app.route("/api/summary", methods=["GET"])
def get_summary():
    try:
        data = require_dataset()
        df = data["cleaned"]

        numeric_summary = summary(df)

        if isinstance(numeric_summary, pd.DataFrame):
            numeric_summary = (
                numeric_summary
                .reset_index()
                .to_dict(orient="records")
            )

        elif isinstance(numeric_summary, pd.Series):
            numeric_summary = numeric_summary.to_dict()

        elif not isinstance(
            numeric_summary,
            (dict, list),
        ):
            numeric_summary = []

        return jsonify(
            {
                "ok": True,
                "original_profile": profile_dict(
                    data["raw"]
                ),
                "profile": profile_dict(df),
                "summary": numeric_summary,
                "preview": preview_records(df),
            }
        )

    except (DataFileError, ValueError) as exc:
        return error_response(exc)

    except Exception as exc:
        app.logger.exception("Summary generation failed")

        return error_response(
            f"Could not calculate the summary: {exc}",
            500,
        )


# =========================================================
# TREND ANALYSIS
# =========================================================

@app.route("/api/trend", methods=["POST"])
def get_trend():
    try:
        df = current_df()
        payload = request.get_json(silent=True) or {}

        date_col = payload.get("date_col")
        value_col = payload.get("value_col")
        freq = payload.get("freq", "D")

        if date_col not in df.columns:
            return error_response(
                "Choose a valid date column."
            )

        if value_col not in df.columns:
            return error_response(
                "Choose a valid numeric column."
            )

        result = trend(
            df,
            date_col,
            value_col,
            freq,
        )

        state()["trend"] = result

        if isinstance(result, pd.DataFrame):
            result_df = result.copy()

            columns = [
                str(column)
                for column in result_df.columns
            ]

            safe_df = result_df.astype(object).where(
                pd.notna(result_df),
                None,
            )

            rows = safe_df.values.tolist()

            result_data = result_df.to_dict(
                orient="records"
            )

        elif isinstance(result, dict):
            columns = result.get("columns", [])
            rows = result.get("rows", [])
            result_data = result

        else:
            raise ValueError(
                "The trend function returned an unsupported result."
            )

        return jsonify(
            {
                "ok": True,
                "columns": columns,
                "rows": rows,
                "data": result_data,
            }
        )

    except (
        DataFileError,
        QueryError,
        ValueError,
    ) as exc:
        return error_response(exc)

    except Exception as exc:
        app.logger.exception("Trend analysis failed")

        return error_response(
            f"Could not generate the trend: {exc}",
            500,
        )


# =========================================================
# SQL LAB
# =========================================================

@app.route("/api/sql", methods=["POST"])
def sql_query():
    try:
        df = current_df()
        payload = request.get_json(silent=True) or {}

        query = (
            payload.get("query") or ""
        ).strip()

        if not query:
            return error_response(
                "Enter a SQL query first."
            )

        result = run_sql(df, query)

        if isinstance(result, pd.DataFrame):
            result_df = result

        elif (
            isinstance(result, dict)
            and "columns" in result
            and "rows" in result
        ):
            rows = result["rows"]

            return jsonify(
                {
                    "ok": True,
                    "columns": result["columns"],
                    "rows": rows[:500],
                    "row_count": len(rows),
                    "truncated": len(rows) > 500,
                }
            )

        else:
            return error_response(
                "The SQL engine returned an unsupported result."
            )

        truncated = len(result_df) > 500

        limited = result_df.head(500).copy()

        limited = limited.astype(object).where(
            pd.notna(limited),
            None,
        )

        return jsonify(
            {
                "ok": True,
                "columns": [
                    str(column)
                    for column in limited.columns
                ],
                "rows": limited.values.tolist(),
                "row_count": int(len(result_df)),
                "truncated": truncated,
            }
        )

    except (
        DataFileError,
        QueryError,
        ValueError,
    ) as exc:
        return error_response(exc)

    except Exception as exc:
        app.logger.exception("SQL query failed")

        return error_response(
            f"SQL query failed: {exc}",
            500,
        )


# =========================================================
# EXPORT CSV
# =========================================================

@app.route("/api/export/csv", methods=["GET"])
def download_csv():
    try:
        df = current_df()
        output = export_csv(df)

        if isinstance(output, bytes):
            output = io.BytesIO(output)

        if not hasattr(output, "seek"):
            raise ValueError(
                "The CSV export function must return bytes or a file-like object."
            )

        output.seek(0)

        return send_file(
            output,
            mimetype="text/csv",
            as_attachment=True,
            download_name="clear_metrics_export.csv",
        )

    except (DataFileError, ValueError) as exc:
        return error_response(exc)

    except Exception as exc:
        app.logger.exception("CSV export failed")

        return error_response(
            f"CSV export failed: {exc}",
            500,
        )


# =========================================================
# EXPORT EXCEL REPORT
# =========================================================

@app.route("/api/export/excel", methods=["GET"])
def download_excel():
    try:
        data = require_dataset()
        df = data["cleaned"]

        summary_result = summary(df)

        if isinstance(summary_result, pd.DataFrame):
            summary_df = summary_result
        else:
            summary_df = pd.DataFrame(
                summary_result
                if isinstance(summary_result, (dict, list))
                else {}
            )

        trend_result = data.get("trend")

        if isinstance(trend_result, pd.DataFrame):
            trend_df = trend_result
        else:
            trend_df = None

        output = export_excel(
            df,
            summary_df,
            trend_df,
        )

        if isinstance(output, bytes):
            output = io.BytesIO(output)

        if not hasattr(output, "seek"):
            raise ValueError(
                "The Excel export function must return bytes or a file-like object."
            )

        output.seek(0)

        return send_file(
            output,
            mimetype=(
                "application/vnd.openxmlformats-officedocument."
                "spreadsheetml.sheet"
            ),
            as_attachment=True,
            download_name="clear_metrics_report.xlsx",
        )

    except (DataFileError, ValueError) as exc:
        return error_response(exc)

    except Exception as exc:
        app.logger.exception("Excel export failed")

        return error_response(
            f"Excel export failed: {exc}",
            500,
        )


# =========================================================
# ADDITIONAL DATA QUALITY AND WORKFLOW API ROUTES
# =========================================================

@app.route("/api/quality", methods=["GET"])
def quality_report():
    try:
        df = current_df()
        return jsonify({
            "ok": True,
            "quality": data_quality_report(df),
            "columns": column_profile(df),
        })
    except DataFileError as exc:
        return error_response(exc)


@app.route("/api/columns", methods=["GET"])
def columns_report():
    try:
        return jsonify({"ok": True, "columns": column_profile(current_df())})
    except DataFileError as exc:
        return error_response(exc)


@app.route("/api/validate", methods=["POST"])
def validate_data():
    try:
        df = current_df()
        payload = request.get_json(silent=True) or {}
        rules = payload.get("rules") or {}
        return jsonify({"ok": True, "validation": validate_dataset(df, rules)})
    except DataFileError as exc:
        return error_response(exc)
    except (TypeError, ValueError) as exc:
        return error_response(exc)


@app.route("/api/correlation", methods=["GET"])
def correlation_data():
    try:
        return jsonify({"ok": True, "analysis": correlation_report(current_df())})
    except DataFileError as exc:
        return error_response(exc)


@app.route("/api/filter", methods=["POST"])
def filter_data():
    try:
        data = require_dataset()
        payload = request.get_json(silent=True) or {}
        rules = payload.get("rules", [])
        if not isinstance(rules, list):
            return error_response("'rules' must be a list.")

        filtered = data["cleaned"].copy()
        for rule in rules:
            if not isinstance(rule, dict):
                return error_response("Each filter rule must be an object.")
            filtered = filter_dataset(
                filtered,
                column=rule.get("column", ""),
                operation=rule.get("operation", ""),
                value=rule.get("value"),
                second_value=rule.get("second_value"),
            )

        data["active_filters"] = rules
        safe = filtered.astype(object).where(pd.notna(filtered), None)
        return jsonify({
            "ok": True,
            "applied_rules": len(rules),
            "rows_before": int(len(data["cleaned"])),
            "rows_after": int(len(filtered)),
            "columns": [str(c) for c in filtered.columns],
            "records": safe.to_dict(orient="records"),
            "preview": preview_records(filtered, limit=50),
        })
    except DataFileError as exc:
        return error_response(exc)
    except (TypeError, ValueError, KeyError) as exc:
        return error_response(exc)


@app.route("/api/filter/reset", methods=["POST"])
def reset_filter():
    data = state()
    data["active_filters"] = []
    try:
        df = current_df()
        return jsonify({"ok": True, "preview": preview_records(df, limit=50), "rows": len(df)})
    except DataFileError as exc:
        return error_response(exc)


@app.route("/api/cleaning-history", methods=["GET"])
def cleaning_history():
    history = state().get("history", [])
    return jsonify({
        "ok": True,
        "count": len(history),
        "can_undo": bool(history),
        "history": [
            {"index": i, "created_at": item["created_at"],
             "rows": int(len(item["dataframe"])),
             "columns": int(len(item["dataframe"].columns))}
            for i, item in enumerate(history)
        ],
    })


@app.route("/api/undo-clean", methods=["POST"])
def undo_clean():
    data = state()
    if not data.get("history"):
        return error_response("There is no cleaning operation to undo.", 400)
    previous = data["history"].pop()
    data["cleaned"] = previous["dataframe"].copy()
    data["profile"] = profile_dict(data["cleaned"])
    data["summary"] = None
    data["trend"] = None
    data["clean_log"] = previous.get("log", [])
    return jsonify({
        "ok": True,
        "message": "Last cleaning operation undone.",
        "profile": data["profile"],
        "quality": data_quality_report(data["cleaned"]),
        "preview": preview_records(data["cleaned"], limit=20),
        "history_count": len(data["history"]),
    })


@app.route("/api/compare", methods=["GET"])
def compare_data():
    try:
        data = require_dataset()
        raw_df, cleaned_df = data["raw"], data["cleaned"]
        def stats(frame):
            return {
                "rows": int(len(frame)),
                "columns": int(len(frame.columns)),
                "missing_cells": int(frame.isna().sum().sum()),
                "duplicate_rows": int(frame.duplicated().sum()),
                "quality": data_quality_report(frame),
            }
        return jsonify({
            "ok": True, "before": stats(raw_df), "after": stats(cleaned_df),
            "row_delta": int(len(cleaned_df) - len(raw_df)),
            "missing_delta": int(cleaned_df.isna().sum().sum() - raw_df.isna().sum().sum()),
            "duplicate_delta": int(cleaned_df.duplicated().sum() - raw_df.duplicated().sum()),
            "clean_log": data.get("clean_log", []),
        })
    except DataFileError as exc:
        return error_response(exc)


@app.route("/api/export/report", methods=["GET"])
def download_analysis_report():
    try:
        df = current_df()
        data = state()
        report = {
            "project": "Clear Metrics",
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "filename": data.get("filename"),
            "dataset": profile_dict(df),
            "quality": data_quality_report(df),
            "column_profile": column_profile(df),
            "validation": validate_dataset(df, {}),
            "correlation": correlation_report(df),
            "cleaning_log": data.get("clean_log", []),
        }
        buffer = io.BytesIO(__import__("json").dumps(json_safe(report), indent=2, ensure_ascii=False).encode("utf-8"))
        buffer.seek(0)
        return send_file(buffer, mimetype="application/json", as_attachment=True, download_name="clear_metrics_analysis_report.json")
    except DataFileError as exc:
        return error_response(exc)
    except Exception as exc:
        app.logger.exception("Analysis report export failed")
        return error_response(f"Could not create report: {exc}", 500)


# =========================================================
# FILE SIZE ERROR
# =========================================================

@app.errorhandler(413)
def file_too_large(_error):
    return error_response(
        "File is too large. The maximum upload size is 10 MB.",
        413,
    )


# =========================================================
# START APPLICATION
# =========================================================

if __name__ == "__main__":
    app.run(
        debug=True,
        port=5000,
    )
