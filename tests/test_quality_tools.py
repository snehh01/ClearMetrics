import unittest

import pandas as pd

from quality_tools import (
    column_profile,
    correlation_report,
    data_quality_report,
    filter_dataset,
    validate_dataset,
)


class QualityToolsTests(unittest.TestCase):
    def setUp(self):
        self.df = pd.DataFrame({
            "region": ["West", "East", "West", None],
            "revenue": [100.0, 200.0, 100.0, 900.0],
            "units": [1, 2, 1, 9],
        })

    def test_quality_counts_missing_and_duplicates(self):
        report = data_quality_report(self.df)
        self.assertEqual(report["rows"], 4)
        self.assertEqual(report["columns"], 3)
        self.assertEqual(report["missing_cells"], 1)
        self.assertEqual(report["duplicate_rows"], 1)

    def test_column_profile_has_all_columns(self):
        profile = column_profile(self.df)
        self.assertEqual(len(profile), 3)
        self.assertEqual(profile[0]["column"], "region")

    def test_validation_reports_missing_required_column(self):
        result = validate_dataset(self.df, {
            "required_columns": ["region", "unknown"],
            "numeric": ["revenue"],
        })
        self.assertFalse(result["passed"])
        self.assertEqual(result["checks_total"], 2)

    def test_numeric_filter(self):
        result = filter_dataset(self.df, "revenue", "gte", 200)
        self.assertEqual(result["revenue"].tolist(), [200.0, 900.0])

    def test_string_filter(self):
        result = filter_dataset(self.df, "region", "contains", "est")
        self.assertEqual(result["region"].tolist(), ["West", "West"])

    def test_correlation_has_numeric_fields(self):
        result = correlation_report(self.df)
        self.assertEqual(result["numeric_columns"], ["revenue", "units"])
        self.assertIn("revenue", result["correlation_matrix"])


if __name__ == "__main__":
    unittest.main()
