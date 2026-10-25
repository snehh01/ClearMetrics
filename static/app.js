"use strict";

document.addEventListener("DOMContentLoaded", () => {
    const $ = (selector) => document.querySelector(selector);
    const $$ = (selector) => [...document.querySelectorAll(selector)];

    const state = {
        filename: "",
        hasDataset: false,
        uploading: false,
        columns: [],
        statisticalSummary: { numeric: [], categorical: [] },
        charts: {}
    };

    // =========================================================
    // ELEMENTS
    // =========================================================

    const fileInput = $("#fileInput");
    const uploadForm = $("#uploadForm");
    const uploadButton = $("#uploadButton");
    const selectedFile = $("#selectedFile");
    const sampleButton = $("#sampleButton");

    const cleanForm = $("#cleanForm");
    const cleanButton = $("#cleanButton");
    const insightsButton = $("#insightsButton");

    const sqlQuery = $("#sqlQuery");
    const runSql = $("#runSql");

    const refreshButton = $("#refreshDashboard");
    const themeToggle = $("#themeToggle");

    // =========================================================
    // NOTIFICATIONS
    // =========================================================

    function message(text, type = "info") {
        let toast = $("#appToast");

        if (!toast) {
            toast = document.createElement("div");
            toast.id = "appToast";
            toast.setAttribute("role", "status");

            Object.assign(toast.style, {
                position: "fixed",
                right: "22px",
                bottom: "22px",
                zIndex: "99999",
                maxWidth: "min(420px, calc(100vw - 36px))",
                padding: "14px 18px",
                borderRadius: "12px",
                background: "#171725",
                color: "#ffffff",
                boxShadow: "0 12px 36px rgba(0,0,0,.22)",
                fontSize: "14px",
                lineHeight: "1.5"
            });

            document.body.appendChild(toast);
        }

        toast.textContent = text;
        toast.style.border =
            type === "error"
                ? "1px solid #f87171"
                : type === "success"
                    ? "1px solid #4ade80"
                    : "1px solid #a78bfa";

        toast.hidden = false;

        clearTimeout(toast._timer);

        toast._timer = setTimeout(() => {
            toast.hidden = true;
        }, 4500);
    }

    // =========================================================
    // API HELPER
    // =========================================================

    async function api(url, options = {}) {
        const headers = { ...(options.headers || {}) };

        if (
            options.body &&
            !(options.body instanceof FormData)
        ) {
            headers["Content-Type"] = "application/json";
        }

        const response = await fetch(url, {
            ...options,
            headers
        });

        const contentType =
            response.headers.get("content-type") || "";

        let data;

        if (contentType.includes("application/json")) {
            data = await response.json();
        } else {
            if (!response.ok) {
                throw new Error(
                    `Request failed (${response.status}).`
                );
            }

            return response;
        }

        if (!response.ok || data.ok === false) {
            throw new Error(
                data.error ||
                data.message ||
                data.detail ||
                `Request failed (${response.status}).`
            );
        }

        return data;
    }

    // =========================================================
    // BUTTON LOADING STATES
    // =========================================================

    function busy(button, isBusy, label = "Working...") {
        if (!button) return;

        if (isBusy) {
            if (!button.dataset.originalText) {
                button.dataset.originalText =
                    button.textContent.trim();
            }

            button.disabled = true;
            button.setAttribute("aria-busy", "true");
            button.textContent = label;
        } else {
            button.disabled = false;
            button.removeAttribute("aria-busy");

            if (button.dataset.originalText) {
                button.textContent =
                    button.dataset.originalText;

                delete button.dataset.originalText;
            }
        }
    }

    // =========================================================
    // FORMATTING
    // =========================================================

    function fmt(value) {
        if (
            value === null ||
            value === undefined ||
            value === ""
        ) {
            return "—";
        }

        const number = Number(value);

        return Number.isFinite(number)
            ? new Intl.NumberFormat("en-IN", {
                maximumFractionDigits: 2
            }).format(number)
            : String(value);
    }

    function humanize(value) {
        return String(value || "")
            .replace(/[_-]+/g, " ")
            .replace(/\s+/g, " ")
            .trim()
            .replace(/\b\w/g, (char) => char.toUpperCase());
    }

    // =========================================================
    // DATASET NAME AND METRICS
    // =========================================================

    function setDatasetName(name, rows, columns) {
        const datasetName = $("#currentDataset");
        const description = $("#datasetDescription");

        if (datasetName) {
            datasetName.textContent =
                name || "No dataset loaded";
        }

        if (description) {
            const details = [];

            if (rows !== undefined && rows !== null) {
                details.push(`${fmt(rows)} rows`);
            }

            if (columns !== undefined && columns !== null) {
                details.push(`${fmt(columns)} columns`);
            }

            description.textContent = details.length
                ? details.join(" · ")
                : "Import a CSV or Excel file to get started.";
        }
    }

    function updateProfile(profile = {}, columns = []) {
        const totalRows = $("#totalRows");
        const totalColumns = $("#totalColumns");
        const missingValues = $("#missingValues");
        const duplicateRows = $("#duplicateRows");

        if (totalRows) {
            totalRows.textContent = fmt(profile.rows);
        }

        if (totalColumns) {
            totalColumns.textContent = fmt(
                profile.columns ?? columns.length
            );
        }

        if (missingValues) {
            missingValues.textContent = fmt(
                profile.missing ??
                profile.missing_values
            );
        }

        if (duplicateRows) {
            duplicateRows.textContent = fmt(
                profile.duplicates ??
                profile.duplicate_rows
            );
        }
    }

    function resetDashboard() {
        state.hasDataset = false;
        state.filename = "";
        state.columns = [];
        state.statisticalSummary = { numeric: [], categorical: [] };
        window.clearMetricsDatasetDescription = "";
        buildSqlPresets();

        setDatasetName("No dataset loaded");

        updateProfile({});

        ["trendChart", "categoryChart", "distributionChart", "relationshipChart"].forEach(clearChart);
        renderPreview(null);

        renderInsights([]);
    }

    // =========================================================
    // CHART HELPERS
    // =========================================================

    function hideChartPlaceholder(canvasId, hidden) {
        const placeholderId = canvasId.replace("Chart", "Empty");

        const placeholder = document.getElementById(
            placeholderId
        );

        if (placeholder) {
            placeholder.hidden = hidden;
        }
    }

    function clearChart(canvasId) {
        const canvas = document.getElementById(canvasId);

        if (state.charts[canvasId]) {
            state.charts[canvasId].destroy();
            delete state.charts[canvasId];
        }

        if (canvas) {
            canvas.dataset.chartReady = "false";
        }

        hideChartPlaceholder(canvasId, false);
    }

    function drawChart(
        canvasId,
        type,
        labels,
        values,
        datasetLabel = "Value"
    ) {
        const canvas = document.getElementById(canvasId);

        if (!canvas || typeof Chart === "undefined") {
            console.warn(
                "Chart.js or chart canvas is unavailable:",
                canvasId
            );
            return false;
        }

        if (
            !Array.isArray(labels) ||
            !Array.isArray(values) ||
            labels.length === 0 ||
            values.length === 0
        ) {
            clearChart(canvasId);
            return false;
        }

        const count = Math.min(labels.length, values.length);

        const safeLabels = labels.slice(0, count).map(
            (item) => String(item)
        );

        const safeValues = values.slice(0, count).map(
            (item) => {
                const number = Number(item);
                return Number.isFinite(number) ? number : 0;
            }
        );

        if (state.charts[canvasId]) {
            state.charts[canvasId].destroy();
        }

        const isLine = type === "line";

        state.charts[canvasId] = new Chart(canvas, {
            type,
            data: {
                labels: safeLabels,
                datasets: [{
                    label: datasetLabel,
                    data: safeValues,
                    borderWidth: 2,
                    borderRadius: type === "bar" ? 5 : 0,
                    tension: 0.35,
                    fill: isLine,
                    pointRadius: isLine ? 3 : 0,
                    pointHoverRadius: 5,
                    maxBarThickness: 48
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                animation: {
                    duration: 550
                },
                interaction: {
                    intersect: false,
                    mode: "index"
                },
                plugins: {
                    legend: {
                        display: false
                    },
                    tooltip: {
                        enabled: true
                    }
                },
                scales: {
                    x: {
                        grid: {
                            display: false
                        },
                        ticks: {
                            maxRotation: 35,
                            minRotation: 0,
                            autoSkip: true
                        }
                    },
                    y: {
                        beginAtZero: true,
                        grid: {
                            color: "rgba(130, 140, 160, 0.16)"
                        }
                    }
                }
            }
        });

        canvas.dataset.chartReady = "true";
        hideChartPlaceholder(canvasId, true);

        return true;
    }

    function drawScatterChart(canvasId, points, datasetLabel) {
        const canvas = document.getElementById(canvasId);
        if (!canvas || typeof Chart === "undefined" || !Array.isArray(points) || points.length < 2) {
            clearChart(canvasId);
            return false;
        }
        if (state.charts[canvasId]) state.charts[canvasId].destroy();
        state.charts[canvasId] = new Chart(canvas, {
            type: "scatter",
            data: { datasets: [{ label: datasetLabel, data: points, pointRadius: 3, pointHoverRadius: 5 }] },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { display: false }, tooltip: { enabled: true } },
                scales: {
                    x: { type: "linear", position: "bottom", title: { display: true, text: datasetLabel.split(" vs ")[1] || "X" }, grid: { color: "rgba(130, 140, 160, 0.16)" } },
                    y: { title: { display: true, text: datasetLabel.split(" vs ")[0] || "Y" }, grid: { color: "rgba(130, 140, 160, 0.16)" } }
                }
            }
        });
        canvas.dataset.chartReady = "true";
        hideChartPlaceholder(canvasId, true);
        return true;
    }

    // =========================================================
    // CHART DATA NORMALIZATION
    // Supports common response shapes from the Flask backend.
    // =========================================================

    function normalizeChart(source) {
        if (!source) return null;

        if (Array.isArray(source)) {
            if (!source.length) return null;

            const first = source[0];

            if (
                first &&
                typeof first === "object" &&
                !Array.isArray(first)
            ) {
                const keys = Object.keys(first);
                if (keys.length < 2) return null;

                const labelKey = keys[0];
                const valueKey = keys[1];

                return {
                    labels: source.map(
                        (row) => row[labelKey]
                    ),
                    values: source.map(
                        (row) => row[valueKey]
                    ),
                    title: "",
                    column: valueKey
                };
            }

            return null;
        }

        if (typeof source !== "object") return null;

        const labels =
            source.labels ||
            source.categories ||
            source.x ||
            source.bins;

        const values =
            source.values ||
            source.counts ||
            source.data ||
            source.y ||
            source.frequencies;

        if (
            Array.isArray(labels) &&
            Array.isArray(values) &&
            labels.length &&
            values.length
        ) {
            return {
                labels,
                values,
                title:
                    source.title ||
                    source.name ||
                    "",
                column:
                    source.column ||
                    source.column_name ||
                    source.value_column ||
                    source.category_column ||
                    ""
            };
        }

        return null;
    }

    function firstUsableChart(...candidates) {
        for (const candidate of candidates) {
            const normalized = normalizeChart(candidate);

            if (normalized) {
                return normalized;
            }
        }

        return null;
    }

    function setChartText(canvasId, title, description) {
        const panel = document.getElementById(canvasId)
            ?.closest(".chart-panel");

        if (!panel) return;

        const heading = panel.querySelector("h3");
        const subtitle = panel.querySelector("p");

        if (heading && title) {
            heading.textContent = title;
        }

        if (subtitle && description) {
            subtitle.textContent = description;
        }
    }

    // =========================================================
    // AUTOMATIC CHART SELECTION
    //
    // Prefer a time series when available.
    // Otherwise show numeric distributions and category counts.
    // Do not assume every dataset has dates or categories.
    // =========================================================

    function renderSmartCharts(data) {
        const trend = normalizeChart(data.time_series);
        const category = normalizeChart(data.category_chart);
        const distribution = normalizeChart(data.distribution);
        const relationship = data.relationship_chart && data.relationship_chart.available ? data.relationship_chart : null;

        const title = (id, text) => { const el = document.getElementById(id); if (el) el.textContent = text; };
        const show = (canvas, source, kind, label, emptyMessage) => {
            if (source && source.labels?.length && source.values?.length) {
                drawChart(canvas, kind, source.labels, source.values, label);
                return true;
            }
            clearChart(canvas);
            const empty = document.getElementById(canvas.replace("Chart", "Empty"));
            if (empty) empty.textContent = emptyMessage;
            return false;
        };

        title("trendTitle", trend?.title || "Sales trend");
        title("trendDescription", trend ? `Trend based on ${humanize(data.time_series?.date_column || trend.column || "your data")}.` : "No suitable date column was detected for a time trend.");
        if (trend) show("trendChart", trend, "line", data.time_series?.value_column || trend.column || "Value", "No trend data available.");
        else if (category) { title("trendTitle", category.title || "Category totals"); show("trendChart", category, "bar", data.category_chart?.value_column || "Total", "No chart data available."); }
        else if (distribution) { title("trendTitle", distribution.title || "Value distribution"); show("trendChart", distribution, "line", "Frequency", "No chart data available."); }
        else show("trendChart", null, "bar", "Value", "No time-series or category data is available for this dataset.");

        title("categoryTitle", category?.title || "Category comparison");
        title("categoryDescription", category ? `Comparing ${humanize(data.category_chart?.category_column || category.column || "categories")} by ${humanize(data.category_chart?.value_column || "value")}.` : "No categorical comparison could be generated.");
        show("categoryChart", category, "bar", data.category_chart?.value_column || "Records", "No category data available.");

        title("distributionTitle", distribution?.title || "Value distribution");
        title("distributionDescription", distribution ? `Distribution for ${humanize(data.distribution?.column || distribution.column || "numeric values")}.` : "A distribution requires chart-ready values from the dataset.");
        show("distributionChart", distribution, "bar", "Frequency", "No distribution data available.");

        title("relationshipTitle", relationship?.title || "Numeric relationship");
        title("relationshipDescription", relationship ? `${humanize(relationship.x_column)} vs ${humanize(relationship.y_column)}.` : "A relationship chart requires at least two numeric fields.");
        if (relationship && Array.isArray(relationship.points) && relationship.points.length >= 2) {
            drawScatterChart("relationshipChart", relationship.points, `${relationship.y_column} vs ${relationship.x_column}`);
        } else {
            clearChart("relationshipChart");
            const empty = document.getElementById("relationshipEmpty");
            if (empty) empty.textContent = "This dataset does not have two suitable numeric columns for a relationship chart.";
        }
    }

    function renderPreview(preview) {
        const target = document.getElementById("previewTable");
        const meta = document.getElementById("previewMeta");
        const status = document.getElementById("previewStatus");
        if (!target) return;
        target.replaceChildren();
        const columns = Array.isArray(preview?.columns) ? preview.columns : [];
        const rows = Array.isArray(preview?.rows) ? preview.rows : [];
        if (!columns.length) {
            const empty = document.createElement("div");
            empty.className = "preview-empty";
            empty.textContent = "Upload a CSV or Excel file to preview its contents.";
            target.appendChild(empty);
            if (meta) meta.textContent = "Your uploaded rows will appear here.";
            if (status) status.textContent = "NO DATA";
            return;
        }
        const table = document.createElement("table");
        table.className = "results-table preview-table";
        const thead = document.createElement("thead");
        const header = document.createElement("tr");
        columns.forEach((column) => { const th = document.createElement("th"); th.textContent = String(column); header.appendChild(th); });
        thead.appendChild(header); table.appendChild(thead);
        const tbody = document.createElement("tbody");
        rows.forEach((row) => { const tr = document.createElement("tr"); columns.forEach((_, i) => { const td = document.createElement("td"); const value = Array.isArray(row) ? row[i] : row[columns[i]]; td.textContent = value === null || value === undefined ? "—" : String(value); tr.appendChild(td); }); tbody.appendChild(tr); });
        table.appendChild(tbody); target.appendChild(table);
        if (meta) meta.textContent = `${columns.length} columns · ${rows.length} preview rows`;
        if (status) status.textContent = "PREVIEW READY";
    }

    // =========================================================
    // INSIGHTS
    // =========================================================

    function renderInsights(insights) {
        const target = $("#insightsContainer") || $("#insightsResult");
        if (!target) return;
        const mirror = target.id === "insightsContainer"
            ? $("#insightsResult")
            : $("#insightsContainer");
        target.replaceChildren();

        if (!Array.isArray(insights) || !insights.length) {
            const panel = document.createElement("div");
            panel.className = "panel empty-state";
            const heading = document.createElement("h3");
            heading.textContent = state.hasDataset ? "No additional insights available." : "Load a dataset to get started.";
            const paragraph = document.createElement("p");
            paragraph.textContent = state.hasDataset
                ? "The backend did not return additional insights for this dataset."
                : "Import a CSV or Excel file to explore your data.";
            panel.append(heading, paragraph);
            target.appendChild(panel);
            if (mirror) mirror.replaceChildren(target.firstElementChild.cloneNode(true));
            return;
        }

        // Dataset description card
        const description = document.createElement("article");
        description.className = "panel insight-card";
        const descriptionTitle = document.createElement("h3");
        descriptionTitle.textContent = "What this dataset contains";
        const descriptionText = document.createElement("p");
        descriptionText.textContent = window.clearMetricsDatasetDescription ||
            `The loaded dataset contains ${fmt(state.columns.length)} fields. The observations below are calculated from the current data.`;
        description.append(descriptionTitle, descriptionText);
        target.appendChild(description);

        insights.forEach((item, index) => {
            const card = document.createElement("article");
            card.className = "panel insight-card";
            const number = document.createElement("span");
            number.className = "info-card-icon purple";
            number.textContent = String(index + 1).padStart(2, "0");
            const paragraph = document.createElement("p");
            paragraph.textContent = typeof item === "string"
                ? item
                : item && typeof item === "object"
                    ? (item.message || item.text || item.description || JSON.stringify(item))
                    : String(item);
            card.append(number, paragraph);
            target.appendChild(card);
        });

        const stats = state.statisticalSummary || {};
        const numeric = Array.isArray(stats.numeric) ? stats.numeric : [];
        const categorical = Array.isArray(stats.categorical) ? stats.categorical : [];

        if (numeric.length) {
            const heading = document.createElement("div");
            heading.className = "insight-section-heading";
            heading.innerHTML = "<h2>Numeric summary</h2><p>Calculated from non-missing numeric values.</p>";
            target.appendChild(heading);
            numeric.forEach((item) => {
                const card = document.createElement("article");
                card.className = "panel insight-card statistics-card";
                const title = document.createElement("h3");
                title.textContent = item.column;
                const details = document.createElement("div");
                details.className = "statistics-grid";
                [
                    ["Count", item.count], ["Mean", item.mean], ["Median", item.median],
                    ["Min", item.min], ["Max", item.max], ["Std. dev.", item.std],
                    ["Missing", item.missing]
                ].forEach(([label, value]) => {
                    const cell = document.createElement("div");
                    cell.className = "statistics-cell";
                    const small = document.createElement("span");
                    small.textContent = label;
                    const strong = document.createElement("strong");
                    strong.textContent = fmt(value);
                    cell.append(small, strong);
                    details.appendChild(cell);
                });
                card.append(title, details);
                target.appendChild(card);
            });
        }

        if (categorical.length) {
            const heading = document.createElement("div");
            heading.className = "insight-section-heading";
            heading.innerHTML = "<h2>Category breakdown</h2><p>Most frequent values in each text/category field.</p>";
            target.appendChild(heading);
            categorical.slice(0, 8).forEach((item) => {
                const card = document.createElement("article");
                card.className = "panel insight-card";
                const title = document.createElement("h3");
                title.textContent = `${item.column} · ${fmt(item.unique)} unique values`;
                const list = document.createElement("ul");
                list.className = "category-summary-list";
                (item.top_values || []).forEach((entry) => {
                    const li = document.createElement("li");
                    const value = document.createElement("span");
                    value.textContent = entry.value;
                    const count = document.createElement("strong");
                    count.textContent = fmt(entry.count);
                    li.append(value, count);
                    list.appendChild(li);
                });
                card.append(title, list);
                target.appendChild(card);
            });
        }
        if (mirror) mirror.replaceChildren(...Array.from(target.children, (child) => child.cloneNode(true)));
    }

    function updateCleaningWorkspace(profile = {}, filename = "") {
        const rows = Number(profile.rows || 0);
        const columns = Number(profile.columns || 0);
        const missing = Number(profile.missing ?? profile.missing_values ?? 0);
        const duplicates = Number(profile.duplicates ?? profile.duplicate_rows ?? 0);
        const missingByColumn = profile.missing_by_column || {};
        const completeColumns = Object.values(missingByColumn).filter((count) => Number(count) === 0).length;
        const totalCells = rows * columns;
        const completeness = totalCells ? Math.max(0, Math.min(100, (1 - missing / totalCells) * 100)) : 0;
        const setText = (id, value) => { const el = document.getElementById(id); if (el) el.textContent = value; };
        setText("cleanDatasetName", filename || state.filename || "Active dataset");
        setText("cleanDatasetMeta", `${fmt(rows)} rows · ${fmt(columns)} columns`);
        setText("cleanMissingCount", fmt(missing));
        setText("cleanDuplicateCount", fmt(duplicates));
        setText("cleanCompleteCount", `${completeColumns} / ${columns}`);
        setText("cleanRowCount", fmt(rows));
        const width = (id, percent) => { const el = document.getElementById(id); if (el) el.style.width = `${Math.max(0, Math.min(100, percent))}%`; };
        width("cleanMissingBar", totalCells ? missing / totalCells * 100 : 0);
        width("cleanDuplicateBar", rows ? duplicates / rows * 100 : 0);
        width("cleanCompleteBar", columns ? completeColumns / columns * 100 : 0);
        width("cleanRowsBar", rows ? 100 : 0);
        setText("cleanQualityScore", `${Math.round(completeness)}%`);
        setText("cleanQualityLabel", completeness >= 95 ? "Strong completeness" : completeness >= 80 ? "Good completeness" : "Needs attention");
        setText("cleanQualityDescription", `${fmt(totalCells - missing)} of ${fmt(totalCells)} cells contain values.`);
        const ring = document.getElementById("cleanScoreRing");
        if (ring) ring.style.background = `conic-gradient(var(--primary) ${completeness * 3.6}deg, var(--border) ${completeness * 3.6}deg)`;
        const details = document.getElementById("cleanQualityDetails");
        if (details) {
            const items = Object.entries(missingByColumn).sort((a, b) => Number(b[1]) - Number(a[1])).slice(0, 6);
            details.replaceChildren();
            if (!items.length) {
                const empty = document.createElement("div"); empty.className = "clean-quality-empty"; empty.textContent = "Column-level quality details will appear here."; details.appendChild(empty);
            } else {
                items.forEach(([column, count]) => {
                    const row = document.createElement("div"); row.className = "clean-quality-row";
                    const label = document.createElement("span"); label.textContent = column;
                    const value = document.createElement("strong"); value.textContent = `${fmt(count)} missing`;
                    row.append(label, value); details.appendChild(row);
                });
            }
        }
        setText("cleanBeforeRows", fmt(rows)); setText("cleanBeforeMissing", fmt(missing)); setText("cleanBeforeDuplicates", fmt(duplicates));
        setText("cleanAfterRows", fmt(rows)); setText("cleanAfterMissing", fmt(missing)); setText("cleanAfterDuplicates", fmt(duplicates));
    }

    function updateCleaningComparison(before = {}, after = {}) {
        const setText = (id, value) => { const el = document.getElementById(id); if (el) el.textContent = value; };
        setText("cleanBeforeRows", fmt(before.rows));
        setText("cleanBeforeMissing", fmt(before.missing ?? before.missing_values));
        setText("cleanBeforeDuplicates", fmt(before.duplicates ?? before.duplicate_rows));
        setText("cleanAfterRows", fmt(after.rows));
        setText("cleanAfterMissing", fmt(after.missing ?? after.missing_values));
        setText("cleanAfterDuplicates", fmt(after.duplicates ?? after.duplicate_rows));
    }

    function renderCleaningLog(log) {
        const target = document.getElementById("cleanActivity");
        if (!target) return;
        target.replaceChildren();
        if (!Array.isArray(log) || !log.length) {
            const empty = document.createElement("div"); empty.className = "clean-activity-empty";
            const icon = document.createElement("span"); icon.textContent = "✳";
            const title = document.createElement("strong"); title.textContent = "No cleaning operations yet";
            const paragraph = document.createElement("p"); paragraph.textContent = "Your cleaning log appears here after you run a cleaning operation.";
            empty.append(icon, title, paragraph); target.appendChild(empty); return;
        }
        log.forEach((item) => {
            const row = document.createElement("div"); row.className = "clean-activity-item";
            const icon = document.createElement("span"); icon.className = "activity-check"; icon.textContent = "✓";
            const body = document.createElement("div"); body.className = "clean-activity-copy";
            const title = document.createElement("strong"); title.textContent = item.action || "Cleaning operation";
            const detail = document.createElement("p"); detail.textContent = item.details || "";
            body.append(title, detail); row.append(icon, body); target.appendChild(row);
        });
    }

    // =========================================================
    // RENDER DASHBOARD
    // =========================================================

    function renderDashboard(data) {
        const kpis = data.kpis || {};
        const profile = data.profile || {};

        updateProfile({
            rows: kpis.rows ?? profile.rows,
            columns: kpis.columns ?? profile.columns,
            missing:
                kpis.missing ??
                kpis.missing_values ??
                profile.missing ??
                profile.missing_values,
            duplicates:
                kpis.duplicates ??
                kpis.duplicate_rows ??
                profile.duplicates ??
                profile.duplicate_rows
        }, data.columns || []);

        state.hasDataset = true;
        state.columns = Array.isArray(data.columns) ? data.columns : [];
        state.statisticalSummary = data.statistical_summary || { numeric: [], categorical: [] };
        buildSqlPresets();

        if (data.filename) {
            state.filename = data.filename;
        }

        setDatasetName(
            state.filename || "Dataset loaded",
            kpis.rows ?? profile.rows,
            kpis.columns ?? profile.columns ??
                (data.columns || []).length
        );

        renderSmartCharts(data);
        window.clearMetricsDatasetDescription = data.dataset_description || "";
        renderInsights(data.insights || []);
        updateCleaningWorkspace(data.profile || {}, data.filename || state.filename);
        if ($("#screen-insights")?.classList.contains("active")) refreshQualityReport();
    }

    // =========================================================
    // REFRESH DASHBOARD
    // =========================================================

    async function refreshDashboard() {
        const data = await api("/api/dashboard");
        renderDashboard(data);
        try {
            const summary = await api("/api/summary");
            renderPreview(summary.preview || null);
        } catch (error) {
            console.warn("Dataset preview could not be refreshed:", error);
        }
        return data;
    }

    // =========================================================
    // FILE SELECTION
    // Selecting a file must not replace the active dataset.
    // =========================================================

    if (fileInput) {
        fileInput.addEventListener("change", () => {
            const file = fileInput.files?.[0];

            if (selectedFile) {
                selectedFile.textContent = file
                    ? `Selected: ${file.name}`
                    : "No file selected";
            }

            // Intentionally do not update the current dataset,
            // metrics, or charts until the upload succeeds.
        });
    }

    // =========================================================
    // UPLOAD DATASET
    // =========================================================

    async function uploadFile(file) {
        if (state.uploading) return;

        if (!file) {
            message(
                "Choose a CSV or Excel file before uploading.",
                "error"
            );
            return;
        }

        const extension = file.name
            .split(".")
            .pop()
            .toLowerCase();

        if (!["csv", "xlsx", "xls"].includes(extension)) {
            message(
                "Please choose a CSV or Excel file.",
                "error"
            );
            return;
        }

        state.uploading = true;
        busy(uploadButton, true, "Uploading...");

        if (selectedFile) {
            selectedFile.textContent =
                `Uploading: ${file.name}`;
        }

        try {
            const form = new FormData();
            form.append("file", file);

            const result = await api("/api/upload", {
                method: "POST",
                body: form
            });

            state.filename = result.filename || file.name;
            renderPreview(result.preview || null);
            state.hasDataset = true;

            if (selectedFile) {
                selectedFile.textContent =
                    `Loaded: ${state.filename}`;
            }

            // Get the actual profile and charts from the backend.
            await refreshDashboard();

            message(
                `${state.filename} uploaded successfully.`,
                "success"
            );

            showScreen("overview");
        } catch (error) {
            console.error("Upload failed:", error);

            if (selectedFile) {
                selectedFile.textContent =
                    `Upload failed: ${file.name}`;
            }

            message(
                error.message ||
                    "Upload failed. Check the Flask terminal.",
                "error"
            );
        } finally {
            state.uploading = false;
            busy(uploadButton, false);
        }
    }

    if (uploadForm) {
        uploadForm.addEventListener("submit", async (event) => {
            event.preventDefault();
            await uploadFile(fileInput?.files?.[0]);
        });
    }

    // =========================================================
    // DRAG AND DROP
    // =========================================================

    const dropZone = $("#dropZone");

    if (dropZone) {
        dropZone.addEventListener("dragover", (event) => {
            event.preventDefault();
            dropZone.classList.add("drag-over");
        });

        dropZone.addEventListener("dragleave", () => {
            dropZone.classList.remove("drag-over");
        });

        dropZone.addEventListener("drop", (event) => {
            event.preventDefault();
            dropZone.classList.remove("drag-over");

            const file = event.dataTransfer?.files?.[0];

            if (!file) return;

            if (fileInput) {
                try {
                    const transfer = new DataTransfer();
                    transfer.items.add(file);
                    fileInput.files = transfer.files;
                } catch (error) {
                    console.warn(
                        "Could not populate the file input:",
                        error
                    );
                }
            }

            if (selectedFile) {
                selectedFile.textContent =
                    `Selected: ${file.name}`;
            }

            // Dropping a file selects it. The user still clicks Upload.
        });
    }

    // =========================================================
    // LOAD SAMPLE DATA
    // =========================================================

    if (sampleButton) {
        sampleButton.addEventListener("click", async () => {
            busy(sampleButton, true, "Loading sample...");

            try {
                const result = await api("/api/sample", {
                    method: "POST",
                    body: JSON.stringify({})
                });

                state.filename = result.filename || "sample_data.csv";
                renderPreview(result.preview || null);
                state.hasDataset = true;

                // Clear any previously selected local file.
                if (fileInput) {
                    fileInput.value = "";
                }

                if (selectedFile) {
                    selectedFile.textContent =
                        `Loaded sample: ${state.filename}`;
                }

                await refreshDashboard();

                message(
                    "Sample dataset loaded successfully.",
                    "success"
                );

                showScreen("overview");
            } catch (error) {
                console.error("Sample load failed:", error);

                message(
                    error.message ||
                        "Could not load sample data.",
                    "error"
                );
            } finally {
                busy(sampleButton, false);
            }
        });
    }

    // =========================================================
    // CLEAN DATA
    // =========================================================

    if (cleanForm) {
        cleanForm.addEventListener("submit", async (event) => {
            event.preventDefault();

            const payload = {
                num_strategy: $("#numericStrategy")?.value || "median",
                cat_strategy: $("#categoricalStrategy")?.value || "mode",
                drop_dupes:
                    $("#cleanDuplicates")?.checked ?? true,
                remove_missing:
                    $("#cleanMissing")?.checked ?? false
            };

            busy(cleanButton, true, "Cleaning...");

            try {
                const result = await api("/api/clean", {
                    method: "POST",
                    body: JSON.stringify(payload)
                });

                renderPreview(result.preview || null);
                const cleanResult = $("#cleanResult");
                if (cleanResult) {
                    cleanResult.textContent = result.message || "Cleaning completed successfully.";
                    cleanResult.classList.add("is-success");
                    cleanResult.classList.remove("is-error");
                }
                updateCleaningComparison(result.before || {}, result.after || {});
                renderCleaningLog(result.log || []);
                await refreshDashboard();
                updateCleaningComparison(result.before || {}, result.after || {});
                message("Data cleaning completed.", "success");
            } catch (error) {
                console.error("Cleaning failed:", error);

                message(
                    error.message ||
                        "Cleaning failed.",
                    "error"
                );
            } finally {
                busy(cleanButton, false);
            }
        });
    }

    const cleanMissingToggle = $("#cleanMissing");
    if (cleanMissingToggle) {
        cleanMissingToggle.addEventListener("change", () => {
            const warning = $("#cleanMissingWarning");
            if (warning) warning.hidden = !cleanMissingToggle.checked;
        });
    }

    const cleanHistoryButton = $("#cleanHistoryButton");
    if (cleanHistoryButton) {
        cleanHistoryButton.addEventListener("click", async () => {
            try {
                const result = await api("/api/cleaning-history");
                const target = $("#cleanHistoryResult");
                if (!target) return;
                target.replaceChildren();
                if (!result.history?.length) {
                    target.textContent = "No previous cleaning versions are available yet.";
                    return;
                }
                result.history.slice().reverse().forEach((item) => {
                    const row = document.createElement("div");
                    row.textContent = `${item.created_at} · ${fmt(item.rows)} rows · ${fmt(item.columns)} columns`;
                    target.appendChild(row);
                });
            } catch (error) {
                message(error.message || "Could not load cleaning history.", "error");
            }
        });
    }

    const undoCleanButton = $("#undoCleanButton");
    if (undoCleanButton) {
        undoCleanButton.addEventListener("click", async () => {
            busy(undoCleanButton, true, "Undoing...");
            try {
                const result = await api("/api/undo-clean", { method: "POST", body: JSON.stringify({}) });
                renderPreview(result.preview || null);
                renderCleaningLog([]);
                await refreshDashboard();
                updateCleaningComparison(result.profile || {}, result.profile || {});
                const cleanResult = $("#cleanResult");
                if (cleanResult) {
                    cleanResult.textContent = result.message || "Last cleaning operation undone.";
                    cleanResult.classList.remove("is-error");
                    cleanResult.classList.add("is-success");
                }
                message(result.message || "Last cleaning operation undone.", "success");
            } catch (error) {
                message(error.message || "Nothing to undo.", "error");
            } finally {
                busy(undoCleanButton, false);
            }
        });
    }

    // =========================================================
    // INSIGHTS
    // =========================================================

    if (insightsButton) {
        insightsButton.addEventListener("click", async () => {
            busy(insightsButton, true, "Generating...");

            try {
                const result = await refreshDashboard();
                renderInsights(result.insights || []);

                message("Insights refreshed.", "success");
            } catch (error) {
                console.error("Insights failed:", error);

                message(
                    error.message ||
                        "Load a dataset before generating insights.",
                    "error"
                );
            } finally {
                busy(insightsButton, false);
            }
        });
    }

    // =========================================================
    // SQL RESULTS TABLE
    // =========================================================

    function renderTable(container, columns, rows) {
        if (!container) return;

        container.replaceChildren();

        if (!Array.isArray(rows) || rows.length === 0) {
            container.textContent = "No rows returned.";
            return;
        }

        const table = document.createElement("table");
        table.className = "results-table";

        const header = document.createElement("thead");
        const headerRow = document.createElement("tr");

        columns.forEach((column) => {
            const th = document.createElement("th");
            th.textContent = String(column);
            headerRow.appendChild(th);
        });

        header.appendChild(headerRow);
        table.appendChild(header);

        const body = document.createElement("tbody");

        rows.forEach((row) => {
            const tr = document.createElement("tr");

            const values = Array.isArray(row)
                ? row
                : columns.map((column) => row[column]);

            values.forEach((value) => {
                const td = document.createElement("td");

                td.textContent =
                    value === null || value === undefined
                        ? ""
                        : String(value);

                tr.appendChild(td);
            });

            body.appendChild(tr);
        });

        table.appendChild(body);
        container.appendChild(table);
    }

    // =========================================================
    // SQL QUICK PRESETS (queries use actual loaded column names)
    // =========================================================

    function quoteIdentifier(value) {
        return `"${String(value).replace(/"/g, '""')}"`;
    }

    function buildSqlPresets() {
        const presetButtons = $$("[data-query-type]");
        if (!presetButtons.length) return;
        const columns = state.columns || [];
        const numeric = (state.statisticalSummary?.numeric || []).map((item) => item.column);
        const categorical = (state.statisticalSummary?.categorical || []).map((item) => item.column);
        const firstColumn = columns[0];
        const numericColumn = numeric[0];
        const categoryColumn = categorical[0] || columns.find((column) => !numeric.includes(column));
        const queryFor = (kind) => {
            const q = quoteIdentifier;
            switch (kind) {
                case "top5": return "SELECT * FROM data LIMIT 5;";
                case "last5": return "SELECT * FROM data ORDER BY rowid DESC LIMIT 5;";
                case "rowcount": return "SELECT COUNT(*) AS row_count FROM data;";
                case "missing":
                    if (!columns.length) return "SELECT * FROM data LIMIT 10;";
                    return "SELECT " + columns.map((column) => `SUM(CASE WHEN ${q(column)} IS NULL THEN 1 ELSE 0 END) AS ${q(String(column) + "_missing")}`).join(", ") + " FROM data;";
                case "duplicates":
                    if (!columns.length) return "SELECT * FROM data LIMIT 10;";
                    return `SELECT ${columns.map(q).join(", ")}, COUNT(*) AS duplicate_count FROM data GROUP BY ${columns.map(q).join(", ")} HAVING COUNT(*) > 1 LIMIT 100;`;
                case "sort": return numericColumn ? `SELECT * FROM data ORDER BY ${q(numericColumn)} DESC LIMIT 20;` : `SELECT * FROM data ORDER BY ${q(firstColumn || "rowid")} LIMIT 20;`;
                case "group":
                    if (!categoryColumn) return "SELECT * FROM data LIMIT 10;";
                    return numericColumn
                        ? `SELECT ${q(categoryColumn)}, COUNT(*) AS record_count, AVG(${q(numericColumn)}) AS average_${String(numericColumn).replace(/[^a-zA-Z0-9_]/g, "_")} FROM data GROUP BY ${q(categoryColumn)} ORDER BY record_count DESC LIMIT 20;`
                        : `SELECT ${q(categoryColumn)}, COUNT(*) AS record_count FROM data GROUP BY ${q(categoryColumn)} ORDER BY record_count DESC LIMIT 20;`;
                case "mean": return numericColumn ? `SELECT AVG(${q(numericColumn)}) AS average_${String(numericColumn).replace(/[^a-zA-Z0-9_]/g, "_")} FROM data;` : "SELECT COUNT(*) AS row_count FROM data;";
                case "max": return numericColumn ? `SELECT MAX(${q(numericColumn)}) AS maximum FROM data;` : "SELECT COUNT(*) AS row_count FROM data;";
                case "min": return numericColumn ? `SELECT MIN(${q(numericColumn)}) AS minimum FROM data;` : "SELECT COUNT(*) AS row_count FROM data;";
                case "sum": return numericColumn ? `SELECT SUM(${q(numericColumn)}) AS total FROM data;` : "SELECT COUNT(*) AS row_count FROM data;";
                case "count": return numericColumn ? `SELECT COUNT(${q(numericColumn)}) AS non_null_count FROM data;` : `SELECT COUNT(${q(firstColumn || "*")}) AS non_null_count FROM data;`;
                default: return "SELECT * FROM data LIMIT 10;";
            }
        };
        presetButtons.forEach((button) => {
            button.disabled = !state.hasDataset;
            button.title = state.hasDataset ? "Fill SQL editor with this query" : "Load a dataset first";
            button.onclick = () => {
                if (!state.hasDataset) {
                    message("Load a dataset before using SQL presets.", "error");
                    return;
                }
                if (sqlQuery) sqlQuery.value = queryFor(button.dataset.queryType);
                presetButtons.forEach((other) => other.classList.toggle("active", other === button));
                sqlQuery?.focus();
            };
        });
    }

    // =========================================================
    // RUN SQL
    // =========================================================

    async function executeSql() {
        const query = sqlQuery?.value?.trim();

        if (!query) {
            message("Enter a SQL query first.", "error");
            return;
        }

        busy(runSql, true, "Running...");

        try {
            const result = await api("/api/sql", {
                method: "POST",
                body: JSON.stringify({ query })
            });

            renderTable(
                $("#sqlResult"),
                result.columns || [],
                result.rows || []
            );

            if (result.truncated && $("#sqlResult")) {
                const note = document.createElement("p");

                note.textContent =
                    `Showing the first 500 rows of ${result.row_count} rows.`;

                $("#sqlResult").appendChild(note);
            }

            message(
                `Query completed: ${result.row_count ?? (result.rows || []).length} row(s).`,
                "success"
            );
        } catch (error) {
            console.error("SQL failed:", error);

            const target = $("#sqlResult");

            if (target) {
                target.textContent = error.message;
            }

            message(
                error.message || "SQL query failed.",
                "error"
            );
        } finally {
            busy(runSql, false);
        }
    }

    if (runSql) {
        runSql.addEventListener("click", executeSql);
    }

    if (sqlQuery) {
        sqlQuery.addEventListener("keydown", (event) => {
            if (
                (event.ctrlKey || event.metaKey) &&
                event.key === "Enter"
            ) {
                executeSql();
            }
        });
    }

    // =========================================================
    // EXPORT CSV / EXCEL
    // =========================================================

    $$("[data-export]").forEach((button) => {
        button.addEventListener("click", async () => {
            const kind = button.dataset.export;

            const url = kind === "excel"
                ? "/api/export/excel"
                : "/api/export/csv";

            busy(button, true, "Preparing...");

            try {
                const response = await fetch(url);

                if (!response.ok) {
                    let errorMessage =
                        `Export failed (${response.status}).`;

                    try {
                        const body = await response.json();

                        errorMessage =
                            body.error ||
                            body.message ||
                            body.detail ||
                            errorMessage;
                    } catch (_) {
                        // Use the default message.
                    }

                    throw new Error(errorMessage);
                }

                const blob = await response.blob();
                const objectUrl = URL.createObjectURL(blob);

                const link = document.createElement("a");

                link.href = objectUrl;
                link.download = kind === "excel"
                    ? "clear_metrics_report.xlsx"
                    : "clear_metrics_export.csv";

                document.body.appendChild(link);
                link.click();
                link.remove();

                URL.revokeObjectURL(objectUrl);

                message("Export downloaded.", "success");
            } catch (error) {
                console.error("Export failed:", error);

                message(
                    error.message || "Export failed.",
                    "error"
                );
            } finally {
                busy(button, false);
            }
        });
    });

    // =========================================================
    // NAVIGATION
    // =========================================================

    async function refreshQualityReport() {
        const target = $("#qualityResult");
        if (!target) return;
        try {
            const result = await api("/api/quality");
            const rows = (result.quality?.column_missing || []).map((item) => [
                item.column, item.dtype, item.missing, `${item.missing_percent}%`, item.unique_values
            ]);
            renderTable(target, ["Column", "Type", "Missing", "Missing %", "Unique values"], rows);
        } catch (error) {
            target.textContent = error.message || "Load a dataset to review data quality.";
        }
    }

    function showScreen(name) {
        const validScreens = [
            "overview",
            "upload",
            "clean",
            "insights",
            "sql",
            "export"
        ];

        if (!validScreens.includes(name)) {
            name = "overview";
        }

        $$(".screen").forEach((screen) => {
            const active = screen.id === `screen-${name}`;

            screen.classList.toggle("active", active);
            screen.hidden = !active;
        });

        $$("[data-screen]").forEach((link) => {
            link.classList.toggle(
                "active",
                link.dataset.screen === name
            );
        });

        const labels = {
            overview: "Overview",
            upload: "Import data",
            clean: "Data cleaning",
            insights: "Insights",
            sql: "SQL Lab",
            export: "Export data"
        };

        const breadcrumb = $("#breadcrumbTitle");

        if (breadcrumb) {
            breadcrumb.textContent = labels[name];
        }

        if (location.hash !== `#${name}`) {
            history.replaceState(null, "", `#${name}`);
        }

        $("#sidebar")?.classList.remove("mobile-open");
        if (name === "insights" && state.hasDataset) refreshQualityReport();
    }

    $$("[data-screen]").forEach((link) => {
        link.addEventListener("click", (event) => {
            const name = link.dataset.screen;

            if (!name) return;

            event.preventDefault();
            showScreen(name);
        });
    });

    window.addEventListener("hashchange", () => {
        showScreen(location.hash.replace("#", ""));
    });

    const menuToggle = $("#menuToggle");

    if (menuToggle) {
        menuToggle.addEventListener("click", () => {
            $("#sidebar")?.classList.toggle("mobile-open");
        });
    }

    // =========================================================
    // THEME TOGGLE
    // =========================================================

    function applyTheme(theme) {
        const dark = theme === "dark";
        document.body.classList.toggle("dark", dark);
        document.documentElement.dataset.theme = dark ? "dark" : "light";
        if (themeToggle) {
            themeToggle.setAttribute("aria-pressed", String(dark));
            themeToggle.setAttribute("title", dark ? "Switch to light theme" : "Switch to dark theme");
            themeToggle.textContent = dark ? "☾" : "☼";
        }
    }

    const savedTheme = (() => {
        try { return localStorage.getItem("clearMetricsTheme"); }
        catch (_) { return null; }
    })();
    applyTheme(savedTheme === "dark" ? "dark" : "light");

    if (themeToggle) {
        themeToggle.addEventListener("click", () => {
            const nextTheme = document.body.classList.contains("dark") ? "light" : "dark";
            applyTheme(nextTheme);
            try { localStorage.setItem("clearMetricsTheme", nextTheme); }
            catch (_) { /* Theme still works for this page session. */ }
        });
    }

    // =========================================================
    // MANUAL REFRESH
    // =========================================================

    if (refreshButton) {
        refreshButton.addEventListener("click", async () => {
            busy(refreshButton, true, "Refreshing...");

            try {
                await refreshDashboard();
                message("Dashboard refreshed.", "success");
            } catch (error) {
                console.error("Dashboard refresh failed:", error);

                message(
                    error.message ||
                        "Could not refresh dashboard.",
                    "error"
                );
            } finally {
                busy(refreshButton, false);
            }
        });
    }

    // =========================================================
    // INITIALIZE
    // =========================================================

    buildSqlPresets();

    showScreen(
        location.hash.replace("#", "") || "overview"
    );

    // A 400 response before loading data is expected.
    // It must not cause sample data to load automatically.
    refreshDashboard().catch((error) => {
        console.info(
            "Dashboard not populated yet:",
            error.message
        );

        resetDashboard();
    });

    console.log("Clear Metrics initialized.");
});

