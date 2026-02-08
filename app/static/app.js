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

  function fmtTime(seconds, rangeSeconds) {
    const date = new Date(seconds * 1000);
    if (rangeSeconds >= 3600 * 24 * 180) {
      return date.toLocaleDateString(undefined, { month: "short", year: "2-digit" });
    }
    if (rangeSeconds >= 3600 * 24 * 3) {
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

    const xScale = {
      type: "linear",
      ticks: {
        color: cssVar("--chart-text", "#d9e0f6"),
        callback: (value) => fmtTime(Number(value), rangeSeconds),
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
              `${context.dataset.label}: ${humanize(Number(context.parsed.y))}`,
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
            callback: (value) => humanize(Number(value)),
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

    const rows = payload.summary_rows || [];
    tbody.innerHTML = rows
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
  }

  function pctClass(value) {
    if (typeof value !== "number" || Number.isNaN(value)) {
      return "neutral";
    }
    return value > 0 ? "positive" : value < 0 ? "negative" : "neutral";
  }

  function renderPresetStats(payload) {
    (payload.presets || []).forEach((preset) => {
      const tbody = document.querySelector(`[data-preset-stats='${preset.id}']`);
      if (!(tbody instanceof HTMLElement)) {
        return;
      }

      const rows = preset.stats_rows || [];
      tbody.innerHTML = rows
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
    panel.querySelectorAll("[data-tag-filter]").forEach((element) => {
      if (!(element instanceof HTMLSelectElement)) {
        return;
      }
      const labelName = element.dataset.tagFilter;
      const labelValue = element.value;
      if (!labelName || !labelValue) {
        return;
      }
      labelFilters[labelName] = labelValue;
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
    button.textContent = liveEnabled ? "Stop Live" : "Start Live";
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
    const button = panel.querySelector("[data-action='save']");
    if (!(button instanceof HTMLButtonElement)) {
      return;
    }
    const isUpdate = Boolean((panel.dataset.savedId || "").trim());
    const label = isUpdate ? "Update" : "Save";
    button.dataset.defaultLabel = label;
    setSaveButtonLabel(button, label);
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

    const controlsToWatch = panel.querySelectorAll("[data-control], [data-tag-filter]");
    controlsToWatch.forEach((element) => {
      element.addEventListener("change", () => {
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
      if (!(button instanceof HTMLButtonElement)) {
        return;
      }
      if (button.disabled) {
        return;
      }
      if (!(panel.dataset.metrics || "").trim()) {
        window.alert("Add at least one metric before saving.");
        return;
      }

      const isUpdating = Boolean((panel.dataset.savedId || "").trim());
      let saveTitle = "";
      if (!isUpdating) {
        const promptedName = await promptForSaveName(panel);
        if (typeof promptedName !== "string") {
          return;
        }
        saveTitle = promptedName;
      }

      const defaultLabel = button.dataset.defaultLabel || "Save";
      button.dataset.defaultLabel = defaultLabel;
      button.disabled = true;
      button.classList.remove("saved-ok", "saved-error");
      setSaveButtonLabel(button, "Saving...");
      try {
        const payload = controls(panel);
        if (saveTitle) {
          payload.title = saveTitle;
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
          setSaveButtonLabel(button, button.dataset.defaultLabel || "Save");
          button.classList.remove("saved-ok", "saved-error");
        }, 1400);
      }
    });
  }

  function renderDashboardTile(tile) {
    if (!(tile instanceof HTMLElement)) {
      return;
    }
    const payloadScript = tile.querySelector("[data-role='dashboard-payload']");
    if (!(payloadScript instanceof HTMLScriptElement)) {
      return;
    }

    let payload = null;
    try {
      payload = JSON.parse(payloadScript.textContent || "null");
    } catch {
      payload = null;
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
    );
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

    grid.querySelectorAll("[data-dashboard-item-id]").forEach((tile) => {
      renderDashboardTile(tile);
    });
    bindChartFrameObserver();

    const dashboardId = Number.parseInt(page.dataset.dashboardId || "", 10);
    if (!Number.isFinite(dashboardId) || dashboardId <= 0) {
      return;
    }

    let draggedTile = null;
    let placeholder = null;
    let initialOrder = [];

    const currentOrder = () =>
      Array.from(grid.querySelectorAll("[data-dashboard-item-id]"))
        .map((tile) =>
          Number.parseInt(tile instanceof HTMLElement ? tile.dataset.dashboardItemId || "" : "", 10),
        )
        .filter((value) => Number.isFinite(value) && value > 0);

    const nearestTile = (clientX, clientY) => {
      const candidates = Array.from(grid.querySelectorAll("[data-dashboard-item-id]")).filter(
        (tile) => tile instanceof HTMLElement && tile !== draggedTile,
      );
      if (!candidates.length) {
        return null;
      }
      let best = null;
      let bestScore = Number.POSITIVE_INFINITY;
      candidates.forEach((tile) => {
        if (!(tile instanceof HTMLElement)) {
          return;
        }
        const rect = tile.getBoundingClientRect();
        const centerX = rect.left + rect.width / 2;
        const centerY = rect.top + rect.height / 2;
        const score = Math.hypot(clientX - centerX, clientY - centerY);
        if (score < bestScore) {
          best = tile;
          bestScore = score;
        }
      });
      return best;
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

    grid.addEventListener("dragstart", (event) => {
      const target = event.target instanceof Element ? event.target : null;
      const handle = target?.closest("[data-drag-handle]");
      if (!(handle instanceof HTMLElement)) {
        event.preventDefault();
        return;
      }
      const tile = handle.closest("[data-dashboard-item-id]");
      if (!(tile instanceof HTMLElement)) {
        event.preventDefault();
        return;
      }
      draggedTile = tile;
      initialOrder = currentOrder();
      draggedTile.classList.add("dragging");
      placeholder = document.createElement("article");
      placeholder.className = "dashboard-drop-placeholder";
      placeholder.style.height = `${Math.max(tile.getBoundingClientRect().height, 200)}px`;
      grid.insertBefore(placeholder, tile.nextElementSibling);
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
      const target = nearestTile(event.clientX, event.clientY);
      if (!(target instanceof HTMLElement)) {
        if (placeholder instanceof HTMLElement && !placeholder.isConnected) {
          grid.appendChild(placeholder);
        }
        return;
      }
      const rect = target.getBoundingClientRect();
      const before = event.clientY < rect.top + rect.height / 2;
      if (before) {
        grid.insertBefore(placeholder, target);
      } else {
        grid.insertBefore(placeholder, target.nextElementSibling);
      }
    });

    grid.addEventListener("drop", async (event) => {
      if (!(draggedTile instanceof HTMLElement)) {
        return;
      }
      event.preventDefault();
      if (placeholder instanceof HTMLElement && placeholder.isConnected) {
        grid.insertBefore(draggedTile, placeholder);
      }
      await persistDashboardOrder();
    });

    grid.addEventListener("dragend", async () => {
      if (!(draggedTile instanceof HTMLElement)) {
        return;
      }
      if (placeholder instanceof HTMLElement && placeholder.isConnected) {
        placeholder.remove();
      }
      await persistDashboardOrder();
      draggedTile.classList.remove("dragging");
      draggedTile = null;
      placeholder = null;
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
