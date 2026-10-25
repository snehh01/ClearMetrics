import io
import unittest

try:
    from app import app
except ImportError as exc:  # clear message if dependencies have not been installed
    app = None
    IMPORT_ERROR = exc
else:
    IMPORT_ERROR = None


@unittest.skipIf(app is None, f"App dependencies unavailable: {IMPORT_ERROR}")
class AppSmokeTests(unittest.TestCase):
    def setUp(self):
        app.config["TESTING"] = True
        self.client = app.test_client()

    def test_homepage(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Clear Metrics", response.data)

    def test_sample_and_dashboard(self):
        response = self.client.post("/api/sample")
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertTrue(response.get_json()["ok"])
        dashboard = self.client.get("/api/dashboard")
        self.assertEqual(dashboard.status_code, 200, dashboard.get_data(as_text=True))
        self.assertTrue(dashboard.get_json()["ok"])

    def test_upload_csv(self):
        content = b"region,revenue\nWest,10\nEast,20\n"
        response = self.client.post(
            "/api/upload",
            data={"file": (io.BytesIO(content), "tiny.csv")},
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertTrue(response.get_json()["ok"])


    def test_dashboard_exposes_four_chart_payloads(self):
        loaded = self.client.post("/api/sample")
        self.assertEqual(loaded.status_code, 200, loaded.get_data(as_text=True))
        dashboard = self.client.get("/api/dashboard")
        self.assertEqual(dashboard.status_code, 200, dashboard.get_data(as_text=True))
        payload = dashboard.get_json()
        self.assertIn("time_series", payload)
        self.assertIn("category_chart", payload)
        self.assertIn("distribution", payload)
        self.assertIn("relationship_chart", payload)
        self.assertIsInstance(payload.get("columns"), list)

    def test_summary_preview_uses_column_and_row_arrays(self):
        self.assertEqual(self.client.post("/api/sample").status_code, 200)
        response = self.client.get("/api/summary")
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        preview = response.get_json()["preview"]
        self.assertIsInstance(preview["columns"], list)
        self.assertIsInstance(preview["rows"], list)
        if preview["rows"]:
            self.assertIsInstance(preview["rows"][0], list)

    def test_sql_is_read_only(self):
        loaded = self.client.post("/api/sample")
        self.assertEqual(loaded.status_code, 200)
        response = self.client.post("/api/sql", json={"query": "SELECT * FROM data LIMIT 3"})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertTrue(response.get_json()["ok"])
        denied = self.client.post("/api/sql", json={"query": "DELETE FROM data"})
        self.assertNotEqual(denied.status_code, 200)

    def test_quality_filter_and_undo_workflow(self):
        self.assertEqual(self.client.post("/api/sample").status_code, 200)
        quality = self.client.get("/api/quality")
        self.assertEqual(quality.status_code, 200, quality.get_data(as_text=True))
        self.assertIn("quality", quality.get_json())
        filtered = self.client.post("/api/filter", json={
            "rules": [{"column": "Region", "operation": "eq", "value": "West"}]
        })
        self.assertEqual(filtered.status_code, 200, filtered.get_data(as_text=True))
        cleaned = self.client.post("/api/clean", json={"drop_dupes": True})
        self.assertEqual(cleaned.status_code, 200, cleaned.get_data(as_text=True))
        undone = self.client.post("/api/undo-clean", json={})
        self.assertEqual(undone.status_code, 200, undone.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
