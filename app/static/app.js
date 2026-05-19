(() => {
  const palette = ["#2f80ed", "#27ae60", "#f2994a"];

  let chartMap = {};
  let liveTimer = null;
  let liveEnabled = false;
  let controlDebounceTimer = null;
  let latestPayload = null;
  let activePanelId = 0;
  let chartFrameObserver = null;

  function panelRoot() {
    return document.querySelector("[data-view-panel='1']");
  }

  function cssVar(name, fallback) {
    const value = getComputedStyle(document.body).getPropertyValue(name).trim();
    return value || fallback;
  }

  function isNarrowViewport() {
    return window.matchMedia("(max-width: 720px)").matches;
  }

  function hexToRgba(hex, alpha) {
    const clean = String(hex || "").replace("#", "");
    if (clean.length !== 6) {
      return `rgba(47, 128, 237, ${alpha})`;
    }
    const red = Number.parseInt(clean.slice(0, 2), 16);
    const green = Number.parseInt(clean.slice(2, 4), 16);
    const blue = Number.parseInt(clean.slice(4, 6), 16);
    return `rgba(${red}, ${green}, ${blue}, ${alpha})`;
  }

  function iconPlaySvg() {
    return `
      <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
        <path d="M8 6v12l10-6z" />
      </svg>
    `;
  }

  function iconStopSvg() {
    return `
      <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
        <path d="M7 7h10v10H7z" />
      </svg>
    `;
  }

  function iconGraphSvg() {
    return `
      <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
        <path
          d="M4 18h16M6 16l4-4 3 2 5-6"
          fill="none"
          stroke="currentColor"
          stroke-width="1.8"
          stroke-linecap="round"
          stroke-linejoin="round"
        />
      </svg>
    `;
  }

  function iconNumberSvg() {
    return `
      <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
        <text x="12" y="16" text-anchor="middle">42</text>
      </svg>
    `;
  }

  function setLiveButtonContent(button, running) {
    if (!(button instanceof HTMLButtonElement)) {
      return;
    }
    button.innerHTML = `${running ? iconStopSvg() : iconPlaySvg()}<span>${
      running ? "Stop Live" : "Start Live"
    }</span>`;
  }

  function clearLive() {
    if (liveTimer) {
      window.clearInterval(liveTimer);
      liveTimer = null;
    }
    liveEnabled = false;
  }

  function clearCharts(force = false) {
    Object.entries(chartMap).forEach(([canvasId, chart]) => {
      const disconnected = !chart?.canvas || !chart.canvas.isConnected;
      if (!force && !disconnected) {
        return;
      }
      try {
        chart.destroy();
      } catch (err) {
        console.error("Failed to destroy chart", canvasId, err);
      }
      delete chartMap[canvasId];
    });
  }

  function resizeCharts() {
    Object.values(chartMap).forEach((chart) => {
      try {
        chart.resize();
      } catch (err) {
        console.error("Failed to resize chart", err);
      }
    });
  }

  function isMainChartZoomed() {
    const chart = chartMap["main-chart"];
    if (!chart) {
      return false;
    }

    if (typeof chart.isZoomedOrPanned === "function") {
      try {
        return Boolean(chart.isZoomedOrPanned());
      } catch (err) {
        console.error("Failed to query zoom state", err);
      }
    }

    const xScale = chart.scales?.x;
    if (!xScale) {
      return false;
    }
    const initialMin = Number(xScale.options?.min);
    const initialMax = Number(xScale.options?.max);
    const currentMin = Number(xScale.min);
    const currentMax = Number(xScale.max);
    const epsilon = 1e-6;
    if (!Number.isFinite(initialMin) || !Number.isFinite(initialMax)) {
      return false;
    }
    if (!Number.isFinite(currentMin) || !Number.isFinite(currentMax)) {
      return false;
    }
    return (
      Math.abs(currentMin - initialMin) > epsilon ||
      Math.abs(currentMax - initialMax) > epsilon
    );
  }

  function syncResetZoomButton(panel) {
    if (!(panel instanceof HTMLElement)) {
      return;
    }
    const button = panel.querySelector("[data-action='reset-zoom']");
    if (!(button instanceof HTMLButtonElement)) {
      return;
    }
    button.hidden = !isMainChartZoomed();
  }

  function bindChartFrameObserver() {
    if (typeof window.ResizeObserver !== "function") {
      return;
    }
    if (chartFrameObserver) {
      chartFrameObserver.disconnect();
    }
    chartFrameObserver = new window.ResizeObserver((entries) => {
      entries.forEach((entry) => {
        const frame = entry.target;
        if (!(frame instanceof HTMLElement)) {
          return;
        }
        const canvas = frame.querySelector("canvas");
        if (!(canvas instanceof HTMLCanvasElement)) {
          return;
        }
        const chart = chartMap[canvas.id] || Chart.getChart(canvas);
        if (!chart) {
          return;
        }
        try {
          chart.resize(entry.contentRect.width, entry.contentRect.height);
        } catch (err) {
          console.error("Failed to resize observed chart", canvas.id, err);
        }
      });
    });
    document.querySelectorAll(".chart-frame").forEach((frame) => {
      chartFrameObserver.observe(frame);
    });
  }

  function fmtTime(seconds, rangeSeconds, stepSeconds) {
    const date = new Date(seconds * 1000);
    if (rangeSeconds >= 3600 * 24 * 180) {
      return date.toLocaleDateString(undefined, { month: "short", year: "2-digit" });
    }
    if (rangeSeconds >= 3600 * 24 * 3) {
      if (typeof stepSeconds === "number" && stepSeconds > 0 && stepSeconds < 86400) {
        return date.toLocaleString(undefined, {
          month: "short",
          day: "numeric",
          hour: "2-digit",
          minute: "2-digit",
        });
      }
      return date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
    }
    if (rangeSeconds >= 3600 * 6) {
      return date.toLocaleString(undefined, {
        month: "short",
        day: "numeric",
        hour: "numeric",
      });
    }
    return date.toLocaleTimeString(undefined, {
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  function parseDurationSeconds(duration) {
    if (typeof duration !== "string" || !duration.length) {
      return null;
    }

    const parts = duration.match(/\d+[smhdwy]/g);
    if (!parts || parts.join("") !== duration) {
      return null;
    }

    const multipliers = {
      s: 1,
      m: 60,
      h: 3600,
      d: 86400,
      w: 604800,
      y: 31536000,
    };

    return parts.reduce((total, part) => {
      const unit = part.slice(-1);
      const amount = Number(part.slice(0, -1));
      return total + amount * multipliers[unit];
    }, 0);
  }

  function bucketLabel(stepSeconds) {
    const units = [
      ["week", 604800],
      ["day", 86400],
      ["hour", 3600],
      ["minute", 60],
      ["second", 1],
    ];
    for (const [name, seconds] of units) {
      if (stepSeconds % seconds === 0) {
        const amount = stepSeconds / seconds;
        if (Number.isInteger(amount)) {
          return amount === 1 ? `1 ${name}` : `${amount} ${name}s`;
        }
      }
    }
    return `${Math.round(stepSeconds)} seconds`;
  }

  function fmtTooltipTime(seconds, stepSeconds) {
    const date = new Date(seconds * 1000);
    if (!stepSeconds || stepSeconds <= 0) {
      return date.toLocaleString(undefined, {
        year: "numeric",
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      });
    }

    const label =
      stepSeconds >= 86400
        ? date.toLocaleDateString(undefined, {
            year: "numeric",
            month: "short",
            day: "numeric",
          })
        : date.toLocaleString(undefined, {
            month: "short",
            day: "numeric",
            hour: "2-digit",
            minute: "2-digit",
          });
    return `${bucketLabel(stepSeconds)} starting ${label}`;
  }

  function formatTimingValue(milliseconds) {
    if (typeof milliseconds !== "number" || Number.isNaN(milliseconds)) {
      return "-";
    }
    const absolute = Math.abs(milliseconds);
    if (absolute < 1) {
      const value = milliseconds.toFixed(2).replace(/\.?0+$/, "");
      return `${value}ms`;
    }
    if (absolute < 1000) {
      const decimals = absolute < 10 ? 2 : 0;
      return `${milliseconds.toFixed(decimals)}ms`;
    }
    if (absolute < 60000) {
      return `${(milliseconds / 1000).toFixed(2)}s`;
    }
    return `${(milliseconds / 60000).toFixed(2)}min`;
  }

  function humanize(value) {
    if (typeof value !== "number" || Number.isNaN(value)) {
      return "-";
    }

    const absolute = Math.abs(value);
    const units = [
      [1e12, "T"],
      [1e9, "G"],
      [1e6, "M"],
      [1e3, "K"],
    ];

    for (const [threshold, suffix] of units) {
      if (absolute >= threshold) {
        const scaled = value / threshold;
        return `${scaled.toFixed(Math.abs(scaled) >= 10 ? 1 : 2)}${suffix}`;
      }
    }

    return value.toLocaleString(undefined, { maximumFractionDigits: 3 });
  }

  function valueOrDash(value) {
    if (typeof value !== "number" || Number.isNaN(value)) {
      return null;
    }
    return value;
  }

  function formatPct(value) {
    if (typeof value !== "number" || Number.isNaN(value)) {
      return "-";
    }
    const sign = value > 0 ? "+" : "";
    return `${sign}${value.toFixed(1)}%`;
  }

  function pointsToDataset(points) {
    return (points || []).map((point) => ({ x: point.t, y: point.v }));
  }

  function shiftedPoints(points, seconds) {
    return (points || []).map((point) => ({ x: point.t + seconds, y: point.v }));
  }

  function chartOptions(
    title,
    rangeSeconds,
    stepSeconds,
    stepLabel,
    rangeStart,
    rangeEnd,
    zoomEnabled = false,
    metricType = null,
  ) {
    const narrow = isNarrowViewport();
    const maxTicks =
      rangeSeconds >= 3600 * 24 * 300
        ? 13
        : rangeSeconds >= 3600 * 24 * 180
          ? 9
          : rangeSeconds >= 3600 * 24 * 30
            ? 10
            : 10;

    const formatValue = (value) =>
      metricType === "timing" ? formatTimingValue(Number(value)) : humanize(Number(value));

    const xScale = {
      type: "linear",
      ticks: {
        color: cssVar("--chart-text", "#d9e0f6"),
        callback: (value) => fmtTime(Number(value), rangeSeconds, stepSeconds),
        maxTicksLimit: narrow ? Math.max(4, Math.floor(maxTicks * 0.7)) : maxTicks,
        maxRotation: 0,
      },
      grid: { color: cssVar("--chart-grid", "rgba(191, 204, 255, 0.12)") },
    };
    if (Number.isFinite(rangeStart)) {
      xScale.min = rangeStart;
    }
    if (Number.isFinite(rangeEnd)) {
      xScale.max = rangeEnd;
    }

    return {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      interaction: { mode: "nearest", intersect: false },
      plugins: {
        legend: {
          display: !narrow,
          position: narrow ? "bottom" : "top",
          align: "start",
          maxHeight: narrow ? 54 : undefined,
          labels: {
            color: cssVar("--chart-text", "#d9e0f6"),
            boxWidth: narrow ? 14 : 20,
            font: { size: narrow ? 10 : 12 },
            padding: narrow ? 8 : 12,
            generateLabels(chart) {
              const base = Chart.defaults.plugins.legend.labels.generateLabels(chart);
              if (!narrow) {
                return base;
              }
              return base.map((item) => ({
                ...item,
                text:
                  item.text && item.text.length > 28
                    ? `${item.text.slice(0, 28)}...`
                    : item.text,
              }));
            },
          },
        },
        title: {
          display: false,
          text: title,
          color: cssVar("--chart-title", "#f5f8ff"),
          font: { size: narrow ? 11 : 13 },
        },
        tooltip: {
          callbacks: {
            title: (items) => {
              const first = items?.[0];
              if (!first) {
                return "";
              }
              return fmtTooltipTime(Number(first.parsed.x), stepSeconds);
            },
            label: (context) =>
              `${context.dataset.label}: ${formatValue(context.parsed.y)}`,
            footer: () => (stepLabel ? `Bucket: ${stepLabel}` : ""),
          },
        },
        zoom: zoomEnabled
          ? {
              limits: {
                x: { min: "original", max: "original" },
              },
              zoom: {
                drag: {
                  enabled: true,
                  threshold: 4,
                  backgroundColor: cssVar("--chart-fill-soft", "rgba(111, 159, 255, 0.1)"),
                  borderColor: cssVar("--accent", "#6f9fff"),
                  borderWidth: 1,
                },
                wheel: { enabled: false },
                pinch: { enabled: false },
                mode: "x",
                onZoomComplete: () => {
                  syncResetZoomButton(panelRoot());
                },
              },
              pan: {
                enabled: false,
                mode: "x",
              },
            }
          : undefined,
      },
      scales: {
        x: xScale,
        y: {
          ticks: {
            color: cssVar("--chart-text", "#d9e0f6"),
            callback: (value) => formatValue(value),
          },
          grid: { color: cssVar("--chart-grid", "rgba(191, 204, 255, 0.12)") },
        },
      },
    };
  }

  function renderChart(
    canvasId,
    datasets,
    title,
    rangeSeconds,
    stepSeconds,
    stepLabel,
    rangeStart,
    rangeEnd,
    zoomEnabled = false,
    metricType = null,
  ) {
    const canvas = document.getElementById(canvasId);
    if (!canvas) {
      return;
    }

    const options = chartOptions(
      title,
      rangeSeconds,
      stepSeconds,
      stepLabel,
      rangeStart,
      rangeEnd,
      zoomEnabled,
      metricType,
    );
    const existing = chartMap[canvasId];
    if (existing && existing.canvas === canvas) {
      try {
        existing.data.datasets = datasets;
        existing.options = options;
        existing.update("none");
        window.requestAnimationFrame(() => {
          try {
            existing.resize();
          } catch (err) {
            console.error("Failed to resize updated chart", canvasId, err);
          }
        });
        return;
      } catch (err) {
        console.error("Failed to update chart", canvasId, err);
      }
    }

    if (existing && existing.canvas !== canvas) {
      try {
        existing.destroy();
      } catch (err) {
        console.error("Failed to destroy stale chart", canvasId, err);
      }
      delete chartMap[canvasId];
    }

    const chartJsExisting = Chart.getChart(canvas);
    if (chartJsExisting) {
      try {
        chartJsExisting.destroy();
      } catch (err) {
        console.error("Failed to destroy existing Chart.js instance", canvasId, err);
      }
    }

    try {
      chartMap[canvasId] = new Chart(canvas, {
        type: "line",
        data: { datasets },
        options,
      });
      window.requestAnimationFrame(() => {
        try {
          chartMap[canvasId]?.resize();
        } catch (err) {
          console.error("Failed to resize new chart", canvasId, err);
        }
      });
    } catch (err) {
      console.error("Failed to render chart", canvasId, err);
    }
  }

  function renderSummary(payload) {
    const panel = panelRoot();
    if (!panel) {
      return;
    }

    const tbody = panel.querySelector("[data-summary-body]");
    if (!(tbody instanceof HTMLElement)) {
      return;
    }

    const summaries =
      Array.isArray(payload.metric_summaries) && payload.metric_summaries.length
        ? payload.metric_summaries
        : [
            {
              metric: payload.summary_metric || "",
              rows: payload.summary_rows || [],
            },
          ];
    const showMetricHeader = summaries.length > 1;
    tbody.innerHTML = summaries
      .map((summary) => {
        const rows = Array.isArray(summary.rows) ? summary.rows : [];
        const header = showMetricHeader
          ? `
          <tr class="summary-metric-row">
            <th scope="colgroup" colspan="8">${summary.metric || "Metric"}</th>
          </tr>
        `
          : "";
        const body = rows
          .map((row) => {
            const stats = row.stats || {};
            return `
              <tr>
                <th scope="row">${row.label}</th>
                <td>${humanize(valueOrDash(stats.latest))}</td>
                <td>${humanize(valueOrDash(stats.min))}</td>
                <td>${humanize(valueOrDash(stats.max))}</td>
                <td>${humanize(valueOrDash(stats.median))}</td>
                <td>${humanize(valueOrDash(stats.total))}</td>
                <td>${humanize(valueOrDash(stats.p95))}</td>
                <td>${humanize(valueOrDash(stats.p99))}</td>
              </tr>
            `;
          })
          .join("");
        return `${header}${body}`;
      })
      .join("");
  }

  function pctClass(value) {
    if (typeof value !== "number" || Number.isNaN(value)) {
      return "neutral";
    }
    return value > 0 ? "positive" : value < 0 ? "negative" : "neutral";
  }

  function renderPresetStats(payload) {
    const metricPayloads = Array.isArray(payload.payloads) ? payload.payloads : [];
    const basePresets = Array.isArray(payload.presets) ? payload.presets : [];
    const showMetricHeader = metricPayloads.length > 1;

    basePresets.forEach((basePreset) => {
      const tbody = document.querySelector(`[data-preset-stats='${basePreset.id}']`);
      if (!(tbody instanceof HTMLElement)) {
        return;
      }

      tbody.innerHTML = metricPayloads
        .map((metricPayload) => {
          const metricPreset = (metricPayload.presets || []).find(
            (preset) => preset.id === basePreset.id,
          );
          const rows = metricPreset?.stats_rows || [];
          if (!rows.length) {
            return "";
          }

          const header = showMetricHeader
            ? `
              <tr class="preset-metric-row">
                <th scope="colgroup" colspan="4">${metricPayload.metric}</th>
              </tr>
            `
            : "";
          const rowMarkup = rows
            .map((row) => {
              const deltaClass = pctClass(row.delta_pct);
              return `
                <tr>
                  <th scope="row">${row.label}</th>
                  <td>${humanize(valueOrDash(row.current))}</td>
                  <td>${humanize(valueOrDash(row.previous))}</td>
                  <td class="${deltaClass}">${formatPct(row.delta_pct)}</td>
                </tr>
              `;
            })
            .join("");
          return `${header}${rowMarkup}`;
        })
        .join("");
    });
  }

  function renderPayload(payload) {
    latestPayload = payload;
    const metricPayloads = payload.payloads || [];
    if (!metricPayloads.length) {
      clearCharts(true);
      return;
    }

    const panel = panelRoot();
    if (panel && Array.isArray(payload.metrics)) {
      panel.dataset.metrics = payload.metrics.join(",");
    }

    const primaryPayload = metricPayloads[0];
    const mainDatasets = [];
    metricPayloads.forEach((metricPayload, index) => {
      const color = palette[index % palette.length];
      mainDatasets.push({
        label: `${metricPayload.metric} (${metricPayload.window.label})`,
        data: pointsToDataset(metricPayload.primary.aggregate.points),
        borderColor: color,
        backgroundColor: hexToRgba(color, 0.14),
        fill: false,
        pointRadius: 0,
        borderWidth: 2,
        tension: 0.2,
      });

      if (payload.compare.enabled && metricPayload.compare.chart) {
        mainDatasets.push({
          label: `${metricPayload.metric} (${metricPayload.compare.label})`,
          data: shiftedPoints(
            metricPayload.compare.chart.aggregate.points,
            metricPayload.compare.offset_seconds,
          ),
          borderColor: color,
          backgroundColor: hexToRgba(color, 0.08),
          fill: false,
          pointRadius: 0,
          borderWidth: 1.8,
          borderDash: [6, 6],
          tension: 0.2,
        });
      }
    });

    renderChart(
      "main-chart",
      mainDatasets,
      `${payload.metrics.join(" • ")} • ${payload.window.label} @ ${payload.step.label}`,
      Number(primaryPayload.primary.end) - Number(primaryPayload.primary.start),
      parseDurationSeconds(payload.step.duration),
      payload.step.label,
      Number(primaryPayload.primary.start),
      Number(primaryPayload.primary.end),
      true,
      primaryPayload.metric_type,
    );

    const basePresets = payload.presets || [];
    basePresets.forEach((basePreset) => {
      const datasets = [];
      metricPayloads.forEach((metricPayload, index) => {
        const color = palette[index % palette.length];
        const metricPreset = (metricPayload.presets || []).find(
          (preset) => preset.id === basePreset.id,
        );
        if (!metricPreset) {
          return;
        }

        datasets.push({
          label: metricPayload.metric,
          data: pointsToDataset(metricPreset.chart.aggregate.points),
          borderColor: color,
          backgroundColor: hexToRgba(color, 0.12),
          fill: false,
          pointRadius: 0,
          borderWidth: 1.8,
          tension: 0.2,
        });

        if (metricPreset.previous_chart) {
          datasets.push({
            label: `${metricPayload.metric} (previous)`,
            data: shiftedPoints(
              metricPreset.previous_chart.aggregate.points,
              Number(metricPreset.previous_offset_seconds || 0),
            ),
            borderColor: color,
            backgroundColor: hexToRgba(color, 0.06),
            fill: false,
            pointRadius: 0,
            borderWidth: 1.5,
            borderDash: [5, 5],
            tension: 0.2,
          });
        }
      });

      renderChart(
        `preset-chart-${basePreset.id}`,
        datasets,
        basePreset.label,
        Number(basePreset.chart.end) - Number(basePreset.chart.start),
        parseDurationSeconds(basePreset.step),
        basePreset.step,
        Number(basePreset.chart.start),
        Number(basePreset.chart.end),
        false,
        primaryPayload.metric_type,
      );
    });

    renderSummary(payload);
    renderPresetStats(payload);
    bindChartFrameObserver();
    syncResetZoomButton(panelRoot());
  }

  function parsePayloadFromPanel(panel) {
    const script = panel.querySelector("[data-role='payload']");
    if (!(script instanceof HTMLScriptElement)) {
      return null;
    }

    try {
      return JSON.parse(script.textContent || "null");
    } catch {
      return null;
    }
  }

  function controls(panel) {
    const labelFilters = {};
    panel.querySelectorAll("[data-tag-filter-label]").forEach((element) => {
      if (!(element instanceof HTMLSelectElement)) {
        return;
      }
      const metricName = element.dataset.tagFilterMetric;
      const labelName = element.dataset.tagFilterLabel;
      const labelValue = element.value;
      if (!metricName || !labelName || !labelValue) {
        return;
      }
      if (!labelFilters[metricName]) {
        labelFilters[metricName] = {};
      }
      labelFilters[metricName][labelName] = labelValue;
    });
    const compareToggle = panel.querySelector("[data-control='compare_enabled']");
    const compareEnabled =
      compareToggle instanceof HTMLInputElement && compareToggle.type === "checkbox"
        ? compareToggle.checked
        : false;
    return {
      saved_id: panel.dataset.savedId || "",
      metrics: panel.dataset.metrics || "",
      window_amount: panel.querySelector("[data-control='window_amount']")?.value,
      window_unit: panel.querySelector("[data-control='window_unit']")?.value,
      step_amount: panel.querySelector("[data-control='step_amount']")?.value,
      step_unit: panel.querySelector("[data-control='step_unit']")?.value,
      type_override: panel.querySelector("[data-control='type_override']")?.value || "",
      agg_override: panel.querySelector("[data-control='agg_override']")?.value || "",
      compare_enabled: compareEnabled ? "1" : "0",
      label_filters: JSON.stringify(labelFilters),
    };
  }

  function syncAddForm(form, panel) {
    const savedIdField = form.querySelector("input[name='saved_id']");
    if (savedIdField instanceof HTMLInputElement) {
      savedIdField.value = panel.dataset.savedId || "";
    }

    const metricsInput = form.querySelector("input[name='metrics']");
    if (metricsInput instanceof HTMLInputElement) {
      metricsInput.value = panel.dataset.metrics || metricsInput.value || "";
    }

    const current = controls(panel);
    const keys = [
      "window_amount",
      "window_unit",
      "step_amount",
      "step_unit",
      "compare_enabled",
      "type_override",
      "agg_override",
      "label_filters",
    ];
    keys.forEach((key) => {
      const field = form.querySelector(`input[name='${key}'], select[name='${key}']`);
      if (!(field instanceof HTMLInputElement || field instanceof HTMLSelectElement)) {
        return;
      }
      field.value = current[key] || field.value;
    });
  }

  function syncUrl(panel) {
    const params = new URLSearchParams(window.location.search);
    const current = controls(panel);
    if (current.saved_id) {
      params.set("saved_id", current.saved_id);
    } else {
      params.delete("saved_id");
    }
    params.set("metrics", current.metrics || "");
    params.set("window_amount", current.window_amount || "1");
    params.set("window_unit", current.window_unit || "week");
    params.set("step_amount", current.step_amount || "1");
    params.set("step_unit", current.step_unit || "hour");
    params.set("compare_enabled", current.compare_enabled || "0");
    params.set("type_override", current.type_override || "");
    params.set("agg_override", current.agg_override || "");
    if (current.label_filters && current.label_filters !== "{}") {
      params.set("label_filters", current.label_filters);
    } else {
      params.delete("label_filters");
    }
    const nextUrl = `${window.location.pathname}?${params.toString()}`;
    window.history.replaceState({}, "", nextUrl);
  }

  async function loadPayload(panel) {
    const params = new URLSearchParams(controls(panel));
    const response = await fetch(`/api/view-data?${params.toString()}`);
    const body = await response.json();
    if (!response.ok) {
      throw new Error(body.error || "Failed to load metric data");
    }
    return body;
  }

  async function refresh(panel, panelId) {
    const currentPanel = panelRoot();
    if (panelId !== activePanelId || !panel.isConnected || currentPanel !== panel) {
      return;
    }
    const payload = await loadPayload(panel);
    const latestPanel = panelRoot();
    if (panelId !== activePanelId || !panel.isConnected || latestPanel !== panel) {
      return;
    }
    renderPayload(payload);
    syncUrl(panel);
  }

  function scheduleRefresh(panel, panelId) {
    if (controlDebounceTimer) {
      window.clearTimeout(controlDebounceTimer);
    }
    controlDebounceTimer = window.setTimeout(async () => {
      try {
        await refresh(panel, panelId);
      } catch (err) {
        renderError(err);
      }
    }, 280);
  }

  function setLiveButtonState(panel) {
    const button = panel.querySelector("[data-action='live']");
    if (!button) {
      return;
    }
    setLiveButtonContent(
      button instanceof HTMLButtonElement ? button : null,
      liveEnabled,
    );
    button.classList.toggle("live-on", liveEnabled);
  }

  function renderError(err) {
    if (err instanceof Error) {
      window.alert(err.message);
      return;
    }
    window.alert("Failed to load metric data");
  }

  function setSaveButtonLabel(button, label) {
    const text = button.querySelector("[data-save-label]");
    if (text instanceof HTMLElement) {
      text.textContent = label;
      return;
    }
    button.textContent = label;
  }

  async function promptForSaveName(panel) {
    const dialog = document.querySelector("[data-save-name-dialog]");
    const fallbackName = (panel.dataset.metrics || "")
      .split(",")
      .map((item) => item.trim())
      .filter(Boolean)
      .join(" • ");
    if (!(dialog instanceof HTMLDialogElement)) {
      const fallback = window.prompt("Name this saved stat", fallbackName);
      const value = typeof fallback === "string" ? fallback.trim() : "";
      return value || null;
    }

    const input = dialog.querySelector("[data-save-name-input]");
    if (!(input instanceof HTMLInputElement)) {
      return null;
    }
    const closeButton = dialog.querySelector("[data-action='close-save-name-modal']");
    if (closeButton instanceof HTMLButtonElement) {
      closeButton.onclick = () => {
        dialog.close("cancel");
      };
    }

    input.value = fallbackName;
    window.requestAnimationFrame(() => {
      input.focus();
      input.select();
    });

    const result = await new Promise((resolve) => {
      const onClose = () => {
        const returnValue = dialog.returnValue;
        if (returnValue !== "confirm") {
          resolve(null);
          return;
        }
        const value = input.value.trim();
        resolve(value || null);
      };
      dialog.addEventListener("close", onClose, { once: true });
      dialog.showModal();
    });

    return typeof result === "string" ? result : null;
  }

  function syncSaveButtonMode(panel) {
    const isUpdate = Boolean((panel.dataset.savedId || "").trim());
    const saveButton = panel.querySelector("[data-action='save']");
    if (saveButton instanceof HTMLButtonElement) {
      const label = isUpdate ? "Update" : "Save";
      saveButton.dataset.defaultLabel = label;
      setSaveButtonLabel(saveButton, label);
    }
    const saveNewButton = panel.querySelector("[data-action='save-new']");
    if (saveNewButton instanceof HTMLButtonElement) {
      saveNewButton.hidden = !isUpdate;
      saveNewButton.dataset.defaultLabel = "Save New";
      setSaveButtonLabel(saveNewButton, "Save New");
    }
  }

  async function submitSave(panel, button, options = {}) {
    const forceCreate = Boolean(options.forceCreate);
    if (!(button instanceof HTMLButtonElement) || button.disabled) {
      return;
    }
    if (!(panel.dataset.metrics || "").trim()) {
      window.alert("Add at least one metric before saving.");
      return;
    }

    const isUpdating = Boolean((panel.dataset.savedId || "").trim());
    let saveTitle = "";
    if (forceCreate || !isUpdating) {
      const promptedName = await promptForSaveName(panel);
      if (typeof promptedName !== "string") {
        return;
      }
      saveTitle = promptedName;
    }

    const defaultLabel = button.dataset.defaultLabel || (forceCreate ? "Save New" : "Save");
    button.dataset.defaultLabel = defaultLabel;
    button.disabled = true;
    button.classList.remove("saved-ok", "saved-error");
    setSaveButtonLabel(button, "Saving...");
    try {
      const payload = controls(panel);
      if (saveTitle) {
        payload.title = saveTitle;
      }
      if (forceCreate) {
        payload.saved_id = "";
        payload.save_as_new = "1";
      }
      const response = await fetch("/api/saved", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Accept: "application/json",
        },
        body: JSON.stringify(payload),
      });
      const body = await response.json();
      if (!response.ok) {
        throw new Error(body.error || "Failed to save view");
      }

      if (typeof body.id === "number") {
        panel.dataset.savedId = String(body.id);
        syncUrl(panel);
        syncSaveButtonMode(panel);
      }
      setSaveButtonLabel(button, body.created ? "Saved" : "Updated");
      button.classList.add("saved-ok");
    } catch (err) {
      setSaveButtonLabel(button, "Failed");
      button.classList.add("saved-error");
      renderError(err);
    } finally {
      button.disabled = false;
      window.setTimeout(() => {
        if (!(button instanceof HTMLButtonElement) || !button.isConnected) {
          return;
        }
        setSaveButtonLabel(button, button.dataset.defaultLabel || (forceCreate ? "Save New" : "Save"));
        button.classList.remove("saved-ok", "saved-error");
      }, 1400);
    }
  }

  function bindPanel(panel) {
    activePanelId += 1;
    const panelId = activePanelId;

    const payload = parsePayloadFromPanel(panel);
    if (payload) {
      renderPayload(payload);
      syncUrl(panel);
    } else {
      refresh(panel, panelId).catch((err) => renderError(err));
    }
    syncSaveButtonMode(panel);

    const controlsToWatch = panel.querySelectorAll("[data-control], [data-tag-filter-label]");
    controlsToWatch.forEach((element) => {
      element.addEventListener("change", () => {
        scheduleRefresh(panel, panelId);
      });
    });

    panel.querySelectorAll("[data-preset-focus='1']").forEach((button) => {
      if (!(button instanceof HTMLButtonElement)) {
        return;
      }
      button.addEventListener("click", () => {
        const windowAmount = button.dataset.windowAmount || "";
        const windowUnit = button.dataset.windowUnit || "";
        const stepAmount = button.dataset.stepAmount || "";
        const stepUnit = button.dataset.stepUnit || "";

        const windowAmountField = panel.querySelector("[data-control='window_amount']");
        const windowUnitField = panel.querySelector("[data-control='window_unit']");
        const stepAmountField = panel.querySelector("[data-control='step_amount']");
        const stepUnitField = panel.querySelector("[data-control='step_unit']");

        if (windowAmountField instanceof HTMLInputElement && windowAmount) {
          windowAmountField.value = windowAmount;
        }
        if (windowUnitField instanceof HTMLSelectElement && windowUnit) {
          windowUnitField.value = windowUnit;
        }
        if (stepAmountField instanceof HTMLInputElement && stepAmount) {
          stepAmountField.value = stepAmount;
        }
        if (stepUnitField instanceof HTMLSelectElement && stepUnit) {
          stepUnitField.value = stepUnit;
        }
        scheduleRefresh(panel, panelId);
      });
    });

    panel.querySelector("[data-action='refresh']")?.addEventListener("click", async () => {
      try {
        await refresh(panel, panelId);
      } catch (err) {
        renderError(err);
      }
    });

    panel.querySelector("[data-action='reset-zoom']")?.addEventListener("click", () => {
      const mainChart = chartMap["main-chart"];
      if (!mainChart) {
        syncResetZoomButton(panel);
        return;
      }
      if (typeof mainChart.resetZoom === "function") {
        try {
          mainChart.resetZoom();
          syncResetZoomButton(panel);
          return;
        } catch (err) {
          console.error("Failed to reset zoom", err);
        }
      }
      mainChart.update("none");
      syncResetZoomButton(panel);
    });

    panel.querySelector("[data-action='live']")?.addEventListener("click", async () => {
      if (liveEnabled) {
        clearLive();
        setLiveButtonState(panel);
        return;
      }

      const seconds = Number(panel.dataset.liveRefreshSeconds || "15");
      clearLive();
      liveEnabled = true;
      setLiveButtonState(panel);

      try {
        await refresh(panel, panelId);
      } catch (err) {
        clearLive();
        setLiveButtonState(panel);
        renderError(err);
        return;
      }

      liveTimer = window.setInterval(async () => {
        try {
          await refresh(panel, panelId);
        } catch (err) {
          clearLive();
          setLiveButtonState(panel);
          renderError(err);
        }
      }, seconds * 1000);
    });

    panel.querySelector("[data-action='save']")?.addEventListener("click", async () => {
      const button = panel.querySelector("[data-action='save']");
      await submitSave(panel, button, { forceCreate: false });
    });

    panel.querySelector("[data-action='save-new']")?.addEventListener("click", async () => {
      const button = panel.querySelector("[data-action='save-new']");
      await submitSave(panel, button, { forceCreate: true });
    });
  }

  function parseDashboardPayloadScript(tile) {
    if (!(tile instanceof HTMLElement)) {
      return null;
    }
    const payloadScript = tile.querySelector("[data-role='dashboard-payload']");
    if (!(payloadScript instanceof HTMLScriptElement)) {
      return null;
    }

    try {
      return JSON.parse(payloadScript.textContent || "null");
    } catch {
      return null;
    }
  }

  function parseDashboardTileConfig(tile) {
    if (!(tile instanceof HTMLElement)) {
      return null;
    }
    const metrics = (tile.dataset.dashboardMetrics || "")
      .split(",")
      .map((metric) => metric.trim())
      .filter(Boolean);
    if (!metrics.length) {
      return null;
    }

    let labelFilters = {};
    const rawLabelFilters = tile.dataset.dashboardLabelFilters || "{}";
    try {
      const parsed = JSON.parse(rawLabelFilters);
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
        labelFilters = parsed;
      }
    } catch {
      labelFilters = {};
    }
    return { metrics, labelFilters };
  }

  function dashboardSavedControlsFromPayload(payload) {
    const windowAmountRaw =
      payload && typeof payload === "object" ? payload.window?.amount : undefined;
    const windowUnitRaw =
      payload && typeof payload === "object" ? payload.window?.unit : undefined;
    const stepAmountRaw = payload && typeof payload === "object" ? payload.step?.amount : undefined;
    const stepUnitRaw = payload && typeof payload === "object" ? payload.step?.unit : undefined;
    const compareRaw = payload && typeof payload === "object" ? payload.compare?.enabled : false;
    const windowAmount = Math.max(1, Number.parseInt(String(windowAmountRaw ?? "1"), 10) || 1);
    const stepAmount = Math.max(1, Number.parseInt(String(stepAmountRaw ?? "1"), 10) || 1);
    const windowUnit = typeof windowUnitRaw === "string" && windowUnitRaw ? windowUnitRaw : "week";
    const stepUnit = typeof stepUnitRaw === "string" && stepUnitRaw ? stepUnitRaw : "hour";
    const compareEnabled = Boolean(compareRaw);
    return {
      window_amount: String(windowAmount),
      window_unit: windowUnit,
      step_amount: String(stepAmount),
      step_unit: stepUnit,
      compare_enabled: compareEnabled ? "1" : "0",
    };
  }

  function setDashboardPayloadScript(tile, payload) {
    if (!(tile instanceof HTMLElement)) {
      return;
    }
    const payloadScript = tile.querySelector("[data-role='dashboard-payload']");
    if (!(payloadScript instanceof HTMLScriptElement)) {
      return;
    }
    try {
      payloadScript.textContent = JSON.stringify(payload);
    } catch (err) {
      console.error("Failed to serialize dashboard payload", err);
    }
  }

  function latestValueFromPoints(points) {
    if (!Array.isArray(points)) {
      return null;
    }
    for (let index = points.length - 1; index >= 0; index -= 1) {
      const value = Number(points[index]?.v);
      if (Number.isFinite(value)) {
        return value;
      }
    }
    return null;
  }

  function renderDashboardLatest(tile, payload) {
    if (!(tile instanceof HTMLElement)) {
      return;
    }
    const latestFrame = tile.querySelector("[data-dashboard-latest]");
    if (!(latestFrame instanceof HTMLElement)) {
      return;
    }
    const metricPayloads =
      payload && typeof payload === "object" && Array.isArray(payload.payloads) ? payload.payloads : [];
    if (!metricPayloads.length) {
      latestFrame.innerHTML = `<div class="empty-box">No data.</div>`;
      return;
    }

    latestFrame.innerHTML = `
      <div class="dashboard-latest-list">
        ${metricPayloads
          .map((metricPayload) => {
            const latest = latestValueFromPoints(metricPayload?.primary?.aggregate?.points);
            const value = latest === null ? "-" : humanize(latest);
            return `
              <div class="dashboard-latest-item">
                <div class="dashboard-latest-name">${metricPayload.metric}</div>
                <div class="dashboard-latest-value">${value}</div>
              </div>
            `;
          })
          .join("")}
      </div>
    `;
  }

  function applyDashboardTileDisplayMode(tile) {
    if (!(tile instanceof HTMLElement)) {
      return;
    }
    const mode = tile.dataset.dashboardDisplayMode === "latest" ? "latest" : "chart";
    const toggle = tile.querySelector("[data-dashboard-display-toggle]");
    const chartFrame = tile.querySelector(".dashboard-chart-frame");
    const latestFrame = tile.querySelector("[data-dashboard-latest]");

    if (toggle instanceof HTMLButtonElement) {
      toggle.setAttribute("aria-pressed", mode === "latest" ? "true" : "false");
      toggle.setAttribute(
        "aria-label",
        mode === "latest" ? "Show graph" : "Show latest value",
      );
      toggle.innerHTML = mode === "latest" ? iconGraphSvg() : iconNumberSvg();
    }
    if (chartFrame instanceof HTMLElement) {
      chartFrame.hidden = mode === "latest";
    }
    if (latestFrame instanceof HTMLElement) {
      latestFrame.hidden = mode !== "latest";
    }
  }

  function renderDashboardTilePayload(tile, payload) {
    if (!(tile instanceof HTMLElement)) {
      return;
    }
    if (!payload || !Array.isArray(payload.payloads) || !payload.payloads.length) {
      return;
    }

    const canvas = tile.querySelector("canvas");
    if (!(canvas instanceof HTMLCanvasElement)) {
      return;
    }

    const metricPayloads = payload.payloads;
    const primaryPayload = metricPayloads[0];
    const datasets = [];
    metricPayloads.forEach((metricPayload, index) => {
      const color = palette[index % palette.length];
      datasets.push({
        label: `${metricPayload.metric} (${metricPayload.window.label})`,
        data: pointsToDataset(metricPayload.primary.aggregate.points),
        borderColor: color,
        backgroundColor: hexToRgba(color, 0.14),
        fill: false,
        pointRadius: 0,
        borderWidth: 2,
        tension: 0.2,
      });

      if (payload.compare?.enabled && metricPayload.compare?.chart) {
        datasets.push({
          label: `${metricPayload.metric} (${metricPayload.compare.label})`,
          data: shiftedPoints(
            metricPayload.compare.chart.aggregate.points,
            metricPayload.compare.offset_seconds,
          ),
          borderColor: color,
          backgroundColor: hexToRgba(color, 0.08),
          fill: false,
          pointRadius: 0,
          borderWidth: 1.8,
          borderDash: [6, 6],
          tension: 0.2,
        });
      }
    });

    renderChart(
      canvas.id,
      datasets,
      `${(payload.metrics || []).join(" • ")} • ${payload.window.label} @ ${payload.step.label}`,
      Number(primaryPayload.primary.end) - Number(primaryPayload.primary.start),
      parseDurationSeconds(payload.step.duration),
      payload.step.label,
      Number(primaryPayload.primary.start),
      Number(primaryPayload.primary.end),
      false,
      primaryPayload.metric_type,
    );
    renderDashboardLatest(tile, payload);
    applyDashboardTileDisplayMode(tile);
  }

  function renderDashboardTile(tile) {
    const payload = parseDashboardPayloadScript(tile);
    renderDashboardTilePayload(tile, payload);
  }

  function initDashboardPage() {
    const page = document.querySelector("[data-dashboard-page='1']");
    if (!(page instanceof HTMLElement)) {
      return;
    }
    const grid = page.querySelector("[data-dashboard-grid]");
    if (!(grid instanceof HTMLElement)) {
      return;
    }

    const dashboardId = Number.parseInt(page.dataset.dashboardId || "", 10);
    const displayStateKey =
      Number.isFinite(dashboardId) && dashboardId > 0
        ? `statview-dashboard-display:${dashboardId}`
        : `statview-dashboard-display:${window.location.pathname}`;

    const readDashboardDisplayModes = () => {
      try {
        const raw = window.localStorage.getItem(displayStateKey);
        if (!raw) {
          return {};
        }
        const parsed = JSON.parse(raw);
        if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
          return {};
        }
        return parsed;
      } catch {
        return {};
      }
    };

    const rememberedDisplayModes = readDashboardDisplayModes();
    const persistDashboardDisplayModes = () => {
      try {
        window.localStorage.setItem(displayStateKey, JSON.stringify(rememberedDisplayModes));
      } catch {
        // Ignore localStorage failures.
      }
    };

    const dashboardItemKey = (tile) => {
      if (!(tile instanceof HTMLElement)) {
        return null;
      }
      const itemId = Number.parseInt(tile.dataset.dashboardItemId || "", 10);
      if (!Number.isFinite(itemId) || itemId <= 0) {
        return null;
      }
      return String(itemId);
    };

    grid.querySelectorAll("[data-dashboard-item-id]").forEach((tile) => {
      if (tile instanceof HTMLElement) {
        const itemKey = dashboardItemKey(tile);
        const rememberedMode =
          itemKey && rememberedDisplayModes[itemKey] === "latest" ? "latest" : "chart";
        tile.dataset.dashboardDisplayMode = rememberedMode;
      }
      renderDashboardTile(tile);
    });

    grid.addEventListener("click", (event) => {
      const target = event.target instanceof Element ? event.target : null;
      const toggle = target?.closest("[data-dashboard-display-toggle]");
      if (!(toggle instanceof HTMLButtonElement)) {
        return;
      }
      const tile = toggle.closest("[data-dashboard-item-id]");
      if (!(tile instanceof HTMLElement)) {
        return;
      }
      event.preventDefault();
      tile.dataset.dashboardDisplayMode =
        tile.dataset.dashboardDisplayMode === "latest" ? "chart" : "latest";
      const itemKey = dashboardItemKey(tile);
      if (itemKey) {
        rememberedDisplayModes[itemKey] = tile.dataset.dashboardDisplayMode;
        persistDashboardDisplayModes();
      }
      applyDashboardTileDisplayMode(tile);
      if (tile.dataset.dashboardDisplayMode === "chart") {
        const canvas = tile.querySelector("canvas");
        if (canvas instanceof HTMLCanvasElement) {
          const chart = chartMap[canvas.id] || Chart.getChart(canvas);
          if (chart) {
            window.requestAnimationFrame(() => {
              try {
                chart.resize();
              } catch (err) {
                console.error("Failed to resize dashboard chart after display toggle", err);
              }
            });
          }
        }
      }
    });
    bindChartFrameObserver();

    const controlsRoot = document.querySelector("[data-dashboard-controls='1']");
    const overrideToggle =
      controlsRoot instanceof HTMLElement
        ? controlsRoot.querySelector("[data-dashboard-control='override_enabled']")
        : null;
    const overrideTargets =
      controlsRoot instanceof HTMLElement
        ? Array.from(controlsRoot.querySelectorAll("[data-dashboard-control]")).filter(
            (element) => element !== overrideToggle,
          )
        : [];

    const isDashboardOverrideEnabled = () =>
      overrideToggle instanceof HTMLInputElement &&
      overrideToggle.type === "checkbox" &&
      overrideToggle.checked;

    const setDashboardOverrideState = (enabled) => {
      if (!(controlsRoot instanceof HTMLElement)) {
        return;
      }
      controlsRoot.dataset.overrideEnabled = enabled ? "1" : "0";
      overrideTargets.forEach((element) => {
        if (element instanceof HTMLInputElement || element instanceof HTMLSelectElement) {
          element.disabled = !enabled;
        }
      });
    };

    const dashboardControlValues = () => {
      if (!(controlsRoot instanceof HTMLElement)) {
        return {
          window_amount: "1",
          window_unit: "week",
          step_amount: "1",
          step_unit: "hour",
          compare_enabled: "0",
        };
      }
      const windowAmountField = controlsRoot.querySelector(
        "[data-dashboard-control='window_amount']",
      );
      const stepAmountField = controlsRoot.querySelector("[data-dashboard-control='step_amount']");
      const windowUnitField = controlsRoot.querySelector("[data-dashboard-control='window_unit']");
      const stepUnitField = controlsRoot.querySelector("[data-dashboard-control='step_unit']");
      const compareField = controlsRoot.querySelector("[data-dashboard-control='compare_enabled']");
      const windowAmount =
        windowAmountField instanceof HTMLInputElement
          ? Math.max(1, Number.parseInt(windowAmountField.value || "1", 10) || 1)
          : 1;
      const stepAmount =
        stepAmountField instanceof HTMLInputElement
          ? Math.max(1, Number.parseInt(stepAmountField.value || "1", 10) || 1)
          : 1;
      const compareEnabled =
        compareField instanceof HTMLInputElement && compareField.type === "checkbox"
          ? compareField.checked
          : false;
      return {
        window_amount: String(windowAmount),
        window_unit:
          windowUnitField instanceof HTMLSelectElement
            ? windowUnitField.value || "week"
            : "week",
        step_amount: String(stepAmount),
        step_unit: stepUnitField instanceof HTMLSelectElement ? stepUnitField.value || "hour" : "hour",
        compare_enabled: compareEnabled ? "1" : "0",
      };
    };

    let dashboardRefreshToken = 0;
    let dashboardRefreshTimer = null;
    let dashboardLiveTimer = null;
    let dashboardLiveEnabled = false;
    const dashboardLiveButton =
      controlsRoot instanceof HTMLElement
        ? controlsRoot.querySelector("[data-dashboard-action='live']")
        : null;
    const dashboardRefreshButton =
      controlsRoot instanceof HTMLElement
        ? controlsRoot.querySelector("[data-dashboard-action='refresh']")
        : null;

    const setDashboardLiveButtonState = () => {
      if (!(dashboardLiveButton instanceof HTMLButtonElement)) {
        return;
      }
      setLiveButtonContent(dashboardLiveButton, dashboardLiveEnabled);
      dashboardLiveButton.classList.toggle("live-on", dashboardLiveEnabled);
    };

    const clearDashboardLive = () => {
      if (dashboardLiveTimer) {
        window.clearInterval(dashboardLiveTimer);
        dashboardLiveTimer = null;
      }
      dashboardLiveEnabled = false;
      setDashboardLiveButtonState();
    };

    const restoreDashboardSavedCharts = () => {
      dashboardRefreshToken += 1;
      if (dashboardRefreshTimer) {
        window.clearTimeout(dashboardRefreshTimer);
        dashboardRefreshTimer = null;
      }
      grid.querySelectorAll("[data-dashboard-item-id]").forEach((tile) => {
        renderDashboardTile(tile);
      });
      bindChartFrameObserver();
    };

    const refreshDashboardCharts = async () => {
      const token = ++dashboardRefreshToken;
      const useOverride = isDashboardOverrideEnabled();
      const overrideValues = dashboardControlValues();
      const tiles = Array.from(grid.querySelectorAll("[data-dashboard-item-id]"));
      const results = await Promise.allSettled(
        tiles.map(async (tile) => {
          if (!(tile instanceof HTMLElement)) {
            return null;
          }
          const config = parseDashboardTileConfig(tile);
          if (!config) {
            return null;
          }

          const savedPayload = parseDashboardPayloadScript(tile);
          const savedValues = dashboardSavedControlsFromPayload(savedPayload);
          const values = useOverride ? overrideValues : savedValues;
          const params = new URLSearchParams({
            metrics: config.metrics.join(","),
            window_amount: values.window_amount,
            window_unit: values.window_unit,
            step_amount: values.step_amount,
            step_unit: values.step_unit,
            compare_enabled: values.compare_enabled,
            label_filters: JSON.stringify(config.labelFilters || {}),
          });
          const response = await fetch(`/api/view-data?${params.toString()}`, {
            headers: { Accept: "application/json" },
          });
          const body = await response.json();
          if (!response.ok) {
            throw new Error(body.error || "Failed to refresh dashboard chart");
          }
          return { tile, payload: body, persistAsSaved: !useOverride };
        }),
      );

      if (token !== dashboardRefreshToken) {
        return;
      }

      let hasFailure = false;
      results.forEach((result) => {
        if (result.status === "fulfilled") {
          if (result.value) {
            if (result.value.persistAsSaved) {
              setDashboardPayloadScript(result.value.tile, result.value.payload);
            }
            renderDashboardTilePayload(result.value.tile, result.value.payload);
          }
          return;
        }
        hasFailure = true;
        console.error("Dashboard chart refresh failed", result.reason);
      });
      bindChartFrameObserver();
      if (hasFailure) {
        renderError(new Error("Failed to refresh one or more dashboard charts."));
      }
    };

    const scheduleDashboardRefresh = () => {
      if (!isDashboardOverrideEnabled()) {
        return;
      }
      if (dashboardRefreshTimer) {
        window.clearTimeout(dashboardRefreshTimer);
      }
      dashboardRefreshTimer = window.setTimeout(() => {
        refreshDashboardCharts().catch((err) => renderError(err));
      }, 280);
    };

    if (controlsRoot instanceof HTMLElement) {
      setDashboardLiveButtonState();
      setDashboardOverrideState(isDashboardOverrideEnabled());
      if (overrideToggle instanceof HTMLInputElement) {
        overrideToggle.addEventListener("change", () => {
          const enabled = isDashboardOverrideEnabled();
          setDashboardOverrideState(enabled);
          if (enabled) {
            scheduleDashboardRefresh();
            return;
          }
          restoreDashboardSavedCharts();
        });
      }
      overrideTargets.forEach((element) => {
        element.addEventListener("change", scheduleDashboardRefresh);
        if (element instanceof HTMLInputElement && element.type === "number") {
          element.addEventListener("input", scheduleDashboardRefresh);
        }
      });

      if (dashboardRefreshButton instanceof HTMLButtonElement) {
        dashboardRefreshButton.addEventListener("click", () => {
          refreshDashboardCharts().catch((err) => renderError(err));
        });
      }

      if (dashboardLiveButton instanceof HTMLButtonElement) {
        dashboardLiveButton.addEventListener("click", async () => {
          if (dashboardLiveEnabled) {
            clearDashboardLive();
            return;
          }

          const seconds = Number.parseInt(
            controlsRoot.dataset.liveRefreshSeconds || "15",
            10,
          );
          const intervalMs = Math.max(1, Number.isFinite(seconds) ? seconds : 15) * 1000;
          clearDashboardLive();
          dashboardLiveEnabled = true;
          setDashboardLiveButtonState();

          try {
            await refreshDashboardCharts();
          } catch (err) {
            clearDashboardLive();
            renderError(err);
            return;
          }

          dashboardLiveTimer = window.setInterval(async () => {
            try {
              await refreshDashboardCharts();
            } catch (err) {
              clearDashboardLive();
              renderError(err);
            }
          }, intervalMs);
        });
      }
    }

    if (!Number.isFinite(dashboardId) || dashboardId <= 0) {
      return;
    }

    let draggedTile = null;
    let dragArmedTile = null;
    let initialOrder = [];

    const currentOrder = () =>
      Array.from(grid.querySelectorAll("[data-dashboard-item-id]"))
        .map((tile) =>
          Number.parseInt(tile instanceof HTMLElement ? tile.dataset.dashboardItemId || "" : "", 10),
        )
        .filter((value) => Number.isFinite(value) && value > 0);

    const insertionReference = (clientX, clientY) => {
      const candidates = Array.from(grid.querySelectorAll("[data-dashboard-item-id]")).filter(
        (tile) => tile instanceof HTMLElement && tile !== draggedTile,
      );
      if (!candidates.length) {
        return null;
      }

      let nearest = null;
      let nearestScore = Number.POSITIVE_INFINITY;
      candidates.forEach((tile) => {
        if (!(tile instanceof HTMLElement)) {
          return;
        }
        const rect = tile.getBoundingClientRect();
        const centerX = rect.left + rect.width / 2;
        const centerY = rect.top + rect.height / 2;
        const withinX = clientX >= rect.left && clientX <= rect.right;
        const withinY = clientY >= rect.top && clientY <= rect.bottom;
        const score = withinX && withinY ? 0 : Math.hypot(clientX - centerX, clientY - centerY);
        if (score < nearestScore) {
          nearest = tile;
          nearestScore = score;
        }
      });

      if (!(nearest instanceof HTMLElement)) {
        return null;
      }

      const nearestRect = nearest.getBoundingClientRect();
      const deltaX = clientX - (nearestRect.left + nearestRect.width / 2);
      const deltaY = clientY - (nearestRect.top + nearestRect.height / 2);
      const before = Math.abs(deltaX) > Math.abs(deltaY) ? deltaX < 0 : deltaY < 0;
      return before ? nearest : nearest.nextElementSibling;
    };

    const persistDashboardOrder = async () => {
      const orderedIds = currentOrder();
      if (orderedIds.length === 0) {
        return;
      }
      if (JSON.stringify(orderedIds) === JSON.stringify(initialOrder)) {
        return;
      }

      try {
        const response = await fetch(`/api/dashboards/${dashboardId}/reorder`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Accept: "application/json",
          },
          body: JSON.stringify({ item_ids: orderedIds }),
        });
        const body = await response.json();
        if (!response.ok) {
          throw new Error(body.error || "Failed to reorder dashboard");
        }
        initialOrder = orderedIds;
      } catch (err) {
        renderError(err);
      }
    };

    grid.addEventListener("pointerdown", (event) => {
      const target = event.target instanceof Element ? event.target : null;
      const handle = target?.closest("[data-drag-handle]");
      if (!(handle instanceof HTMLElement)) {
        dragArmedTile = null;
        return;
      }
      const tile = handle.closest("[data-dashboard-item-id]");
      if (!(tile instanceof HTMLElement)) {
        dragArmedTile = null;
        return;
      }
      dragArmedTile = tile;
    });

    grid.addEventListener("pointerup", () => {
      if (!(draggedTile instanceof HTMLElement)) {
        dragArmedTile = null;
      }
    });
    grid.addEventListener("pointercancel", () => {
      if (!(draggedTile instanceof HTMLElement)) {
        dragArmedTile = null;
      }
    });

    grid.addEventListener("dragstart", (event) => {
      const target = event.target instanceof Element ? event.target : null;
      const tile = target?.closest("[data-dashboard-item-id]");
      if (!(tile instanceof HTMLElement) || tile !== dragArmedTile) {
        event.preventDefault();
        return;
      }
      draggedTile = tile;
      initialOrder = currentOrder();
      draggedTile.classList.add("dragging");
      if (event.dataTransfer) {
        event.dataTransfer.effectAllowed = "move";
        event.dataTransfer.setData("text/plain", tile.dataset.dashboardItemId || "");
      }
    });

    grid.addEventListener("dragover", (event) => {
      if (!(draggedTile instanceof HTMLElement)) {
        return;
      }
      event.preventDefault();
      const reference = insertionReference(event.clientX, event.clientY);
      if (reference === draggedTile) {
        return;
      }
      grid.insertBefore(draggedTile, reference);
    });

    grid.addEventListener("drop", (event) => {
      if (!(draggedTile instanceof HTMLElement)) {
        return;
      }
      event.preventDefault();
    });

    grid.addEventListener("dragend", async () => {
      if (!(draggedTile instanceof HTMLElement)) {
        dragArmedTile = null;
        return;
      }
      draggedTile.classList.remove("dragging");
      await persistDashboardOrder();
      draggedTile = null;
      dragArmedTile = null;
    });
  }

  function initDashboardAddModal() {
    const dialog = document.querySelector("[data-add-saved-dialog]");
    if (!(dialog instanceof HTMLDialogElement)) {
      return;
    }

    const openButton = document.querySelector("[data-action='open-add-saved-modal']");
    const closeButton = dialog.querySelector("[data-action='close-add-saved-modal']");
    if (openButton instanceof HTMLButtonElement) {
      openButton.addEventListener("click", () => {
        dialog.showModal();
      });
    }
    if (closeButton instanceof HTMLButtonElement) {
      closeButton.addEventListener("click", () => {
        dialog.close("cancel");
      });
    }
  }

  function initSavedRenames() {
    const dialog = document.querySelector("[data-rename-dialog]");
    if (!(dialog instanceof HTMLDialogElement)) {
      return;
    }

    const input = dialog.querySelector("[data-rename-input]");
    if (!(input instanceof HTMLInputElement)) {
      return;
    }
    const closeButton = dialog.querySelector("[data-action='close-rename-modal']");
    if (closeButton instanceof HTMLButtonElement) {
      closeButton.addEventListener("click", () => {
        dialog.close("cancel");
      });
    }

    let pendingSavedId = null;
    const openRename = (trigger) => {
      const savedId = Number.parseInt(trigger.dataset.savedId || "", 10);
      if (!Number.isFinite(savedId) || savedId <= 0) {
        return;
      }
      pendingSavedId = savedId;

      const container = trigger.closest("[data-saved-item], [data-dashboard-tile]");
      const currentName = container?.querySelector("[data-saved-title]")?.textContent?.trim() || "";
      input.value = currentName;
      dialog.showModal();
      window.requestAnimationFrame(() => {
        input.focus();
        input.select();
      });
    };

    document.addEventListener("click", (event) => {
      const trigger =
        event.target instanceof Element ? event.target.closest("[data-rename-trigger]") : null;
      if (!(trigger instanceof HTMLButtonElement)) {
        return;
      }
      event.preventDefault();
      openRename(trigger);
    });

    dialog.addEventListener("close", async () => {
      if (dialog.returnValue !== "confirm") {
        pendingSavedId = null;
        return;
      }
      if (!Number.isFinite(pendingSavedId) || pendingSavedId <= 0) {
        pendingSavedId = null;
        return;
      }
      const title = input.value.trim();
      if (!title) {
        pendingSavedId = null;
        return;
      }

      const savedId = pendingSavedId;
      pendingSavedId = null;
      try {
        const response = await fetch(`/api/saved/${savedId}/rename`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Accept: "application/json",
          },
          body: JSON.stringify({ title }),
        });
        const body = await response.json();
        if (!response.ok) {
          throw new Error(body.error || "Failed to rename saved view");
        }

        document.querySelectorAll(`[data-saved-id="${savedId}"] [data-saved-title]`).forEach((el) => {
          el.textContent = body.title;
        });
        document.querySelectorAll("select[name='saved_id'] option").forEach((option) => {
          if (!(option instanceof HTMLOptionElement)) {
            return;
          }
          if (Number.parseInt(option.value || "", 10) === savedId) {
            option.textContent = body.title;
          }
        });
      } catch (err) {
        renderError(err);
      }
    });
  }

  function initThemeToggle() {
    const button = document.querySelector("[data-theme-toggle]");
    if (!(button instanceof HTMLButtonElement)) {
      return;
    }

    const setLabel = () => {
      const theme = document.body.dataset.theme || "dark";
      button.textContent = theme === "dark" ? "Light mode" : "Dark mode";
    };

    setLabel();

    button.addEventListener("click", () => {
      const current = document.body.dataset.theme || "dark";
      const next = current === "dark" ? "light" : "dark";
      document.body.dataset.theme = next;
      localStorage.setItem("statview-theme", next);
      setLabel();
      if (latestPayload) {
        renderPayload(latestPayload);
      }
    });
  }

  function initMetricAddForm() {
    const form = document.querySelector("[data-add-metric-form]");
    if (!(form instanceof HTMLFormElement)) {
      return;
    }

    form.addEventListener("submit", (event) => {
      const panel = panelRoot();
      if (panel) {
        syncAddForm(form, panel);
      }

      const input = form.querySelector("input[name='add_metric']");
      if (!(input instanceof HTMLInputElement)) {
        return;
      }
      const value = input.value.trim();
      if (!value) {
        event.preventDefault();
        return;
      }

      const selected = (panel?.dataset.metrics || "")
        .split(",")
        .map((item) => item.trim())
        .filter(Boolean);
      if (!selected.includes(value) && selected.length >= 3) {
        event.preventDefault();
        window.alert("You can select up to 3 metrics.");
      }
    });
  }

  function ensureSavedEmptyState() {
    const list = document.querySelector("[data-saved-list]");
    if (!(list instanceof HTMLElement)) {
      return;
    }
    if (list.querySelector("[data-saved-item]")) {
      return;
    }
    let empty = document.querySelector("[data-saved-empty]");
    if (!(empty instanceof HTMLElement)) {
      empty = document.createElement("div");
      empty.className = "empty-box";
      empty.setAttribute("data-saved-empty", "1");
      empty.textContent = "No saved views yet.";
      list.insertAdjacentElement("afterend", empty);
    }
  }

  function initSavedDeletes() {
    document.addEventListener("click", async (event) => {
      const trigger =
        event.target instanceof Element ? event.target.closest("[data-saved-delete]") : null;
      if (!(trigger instanceof HTMLButtonElement)) {
        return;
      }
      event.preventDefault();

      const savedId = Number.parseInt(trigger.dataset.savedId || "", 10);
      if (!Number.isFinite(savedId) || savedId <= 0) {
        return;
      }
      if (trigger.disabled) {
        return;
      }

      trigger.disabled = true;
      try {
        const response = await fetch(`/api/saved/${savedId}`, {
          method: "DELETE",
          headers: { Accept: "application/json" },
        });
        const body = await response.json();
        if (!response.ok) {
          throw new Error(body.error || "Failed to delete saved view");
        }

        const row = trigger.closest("[data-saved-item]");
        if (row) {
          row.remove();
        }
        ensureSavedEmptyState();
      } catch (err) {
        renderError(err);
      } finally {
        trigger.disabled = false;
      }
    });
  }

  document.addEventListener("DOMContentLoaded", () => {
    initThemeToggle();
    initMetricAddForm();
    initSavedDeletes();
    initSavedRenames();
    initDashboardAddModal();

    const panel = panelRoot();
    if (panel) {
      bindPanel(panel);
      setLiveButtonState(panel);
      bindChartFrameObserver();
    } else {
      const dashboardPage = document.querySelector("[data-dashboard-page='1']");
      if (!dashboardPage) {
        clearCharts(true);
        clearLive();
      }
    }
    initDashboardPage();

    window.addEventListener("resize", () => {
      resizeCharts();
    });
  });
})();
