(() => {
  const palette = [
    "#2f80ed",
    "#27ae60",
    "#f2994a",
    "#eb5757",
    "#9b51e0",
    "#00b894",
  ];

  let chartMap = {};
  let liveTimer = null;
  let liveEnabled = false;
  let controlDebounceTimer = null;
  let latestPayload = null;
  let activePanelId = 0;

  function panelRoot() {
    return document.querySelector("#metric-panel [data-metric-panel='1']");
  }

  function cssVar(name, fallback) {
    const value = getComputedStyle(document.body).getPropertyValue(name).trim();
    return value || fallback;
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

  function valueOrDash(value) {
    if (typeof value !== "number" || Number.isNaN(value)) {
      return null;
    }
    return value;
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

  function formatPct(value) {
    if (typeof value !== "number" || Number.isNaN(value)) {
      return "-";
    }
    const sign = value > 0 ? "+" : "";
    return `${sign}${value.toFixed(1)}%`;
  }

  function pointsToDataset(points) {
    return points.map((point) => ({ x: point.t, y: point.v }));
  }

  function shiftedPoints(points, seconds) {
    return points.map((point) => ({ x: point.t + seconds, y: point.v }));
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

  function fmtTooltipTime(seconds, stepSeconds, rangeSeconds) {
    const date = new Date(seconds * 1000);
    if (stepSeconds && stepSeconds >= 86400 * 7) {
      return date.toLocaleDateString(undefined, {
        year: "numeric",
        month: "short",
        day: "numeric",
      });
    }
    if (stepSeconds && stepSeconds >= 3600) {
      return date.toLocaleString(undefined, {
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      });
    }
    return fmtTime(seconds, rangeSeconds);
  }

  function chartOptions(title, rangeSeconds, stepSeconds, stepLabel, rangeStart, rangeEnd) {
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
        maxTicksLimit: maxTicks,
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
          labels: {
            color: cssVar("--chart-text", "#d9e0f6"),
          },
        },
        title: {
          display: true,
          text: title,
          color: cssVar("--chart-title", "#f5f8ff"),
          font: { size: 13 },
        },
        tooltip: {
          callbacks: {
            title: (items) => {
              const first = items?.[0];
              if (!first) {
                return "";
              }
              return fmtTooltipTime(Number(first.parsed.x), stepSeconds, rangeSeconds);
            },
            label: (context) =>
              `${context.dataset.label}: ${humanize(Number(context.parsed.y))}`,
            footer: () => (stepLabel ? `Bucket: ${stepLabel}` : ""),
          },
        },
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
    );
    const existing = chartMap[canvasId];
    if (existing && existing.canvas === canvas) {
      try {
        existing.data.datasets = datasets;
        existing.options = options;
        existing.update("none");
        return;
      } catch (err) {
        console.error("Failed to update chart", canvasId, err);
        return;
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

    const mainDatasets = [
      {
        label: `${payload.metric} (${payload.window.label})`,
        data: pointsToDataset(payload.primary.aggregate.points),
        borderColor: palette[0],
        backgroundColor: cssVar("--chart-fill", "rgba(47, 128, 237, 0.18)"),
        fill: true,
        pointRadius: 0,
        borderWidth: 2,
        tension: 0.2,
      },
    ];

    if (payload.compare.enabled && payload.compare.chart) {
      mainDatasets.push({
        label: `${payload.metric} (${payload.compare.label})`,
        data: shiftedPoints(
          payload.compare.chart.aggregate.points,
          payload.compare.offset_seconds,
        ),
        borderColor: palette[0],
        backgroundColor: "rgba(47, 128, 237, 0.08)",
        fill: false,
        pointRadius: 0,
        borderWidth: 2,
        borderDash: [6, 6],
        tension: 0.2,
      });
    }

    renderChart(
      "main-chart",
      mainDatasets,
      `${payload.metric} • ${payload.window.label} @ ${payload.step.label}`,
      Number(payload.primary.end) - Number(payload.primary.start),
      parseDurationSeconds(payload.step.duration),
      payload.step.label,
      Number(payload.primary.start),
      Number(payload.primary.end),
    );

    (payload.presets || []).forEach((preset) => {
      const datasets = [
        {
          label: payload.metric,
          data: pointsToDataset(preset.chart.aggregate.points),
          borderColor: palette[0],
          backgroundColor: cssVar("--chart-fill-soft", "rgba(47, 128, 237, 0.12)"),
          fill: true,
          pointRadius: 0,
          borderWidth: 1.8,
          tension: 0.2,
        },
      ];

      if (preset.previous_chart) {
        datasets.push({
          label: preset.previous_label || "previous window",
          data: shiftedPoints(
            preset.previous_chart.aggregate.points,
            Number(preset.previous_offset_seconds || 0),
          ),
          borderColor: palette[0],
          backgroundColor: "rgba(47, 128, 237, 0.06)",
          fill: false,
          pointRadius: 0,
          borderWidth: 1.6,
          borderDash: [5, 5],
          tension: 0.2,
        });
      }

      renderChart(
        `preset-chart-${preset.id}`,
        datasets,
        preset.label,
        Number(preset.chart.end) - Number(preset.chart.start),
        parseDurationSeconds(preset.step),
        preset.step,
        Number(preset.chart.start),
        Number(preset.chart.end),
      );
    });

    renderSummary(payload);
    renderPresetStats(payload);
  }

  function controls(panel) {
    const compareEnabled = panel.querySelector("[data-control='compare_enabled']");
    const labelFilters = {};
    panel.querySelectorAll("select[data-tag-filter]").forEach((select) => {
      if (!(select instanceof HTMLSelectElement) || !select.value) {
        return;
      }
      const labelName = select.dataset.tagFilter;
      if (!labelName) {
        return;
      }
      labelFilters[labelName] = select.value;
    });

    return {
      metric: panel.dataset.metric,
      window_amount: panel.querySelector("[data-control='window_amount']")?.value,
      window_unit: panel.querySelector("[data-control='window_unit']")?.value,
      step_amount: panel.querySelector("[data-control='step_amount']")?.value,
      step_unit: panel.querySelector("[data-control='step_unit']")?.value,
      compare_enabled:
        compareEnabled instanceof HTMLInputElement && compareEnabled.checked ? "1" : "0",
      compare_offset: panel.querySelector("[data-control='compare_offset']")?.value,
      label_filters: JSON.stringify(labelFilters),
    };
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

  function setCompareControlState(panel) {
    const checkbox = panel.querySelector("[data-control='compare_enabled']");
    const select = panel.querySelector("[data-control='compare_offset']");
    if (!(checkbox instanceof HTMLInputElement) || !(select instanceof HTMLSelectElement)) {
      return;
    }
    select.disabled = !checkbox.checked;
  }

  function syncUrl(panel) {
    const params = new URLSearchParams(window.location.search);
    const current = controls(panel);

    params.set("metric", current.metric || "");
    params.set("window_amount", current.window_amount || "1");
    params.set("window_unit", current.window_unit || "week");
    params.set("step_amount", current.step_amount || "1");
    params.set("step_unit", current.step_unit || "hour");
    params.set("compare_enabled", current.compare_enabled || "0");
    params.set("compare_offset", current.compare_offset || "1w");

    const labelFilters = JSON.parse(current.label_filters || "{}");
    if (Object.keys(labelFilters).length > 0) {
      params.set("label_filters", current.label_filters);
    } else {
      params.delete("label_filters");
    }

    const nextUrl = `${window.location.pathname}?${params.toString()}`;
    window.history.replaceState({}, "", nextUrl);
  }

  async function loadPayload(panel) {
    const params = new URLSearchParams(controls(panel));
    const response = await fetch(`/api/metric-data?${params.toString()}`);
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

  function bindPanel(panel) {
    activePanelId += 1;
    const panelId = activePanelId;
    let allowAutoRefresh = false;
    let userInteractedWithControls = false;
    window.setTimeout(() => {
      allowAutoRefresh = true;
    }, 500);

    const markInteracted = () => {
      userInteractedWithControls = true;
    };
    panel.addEventListener("pointerdown", markInteracted, { passive: true });
    panel.addEventListener("keydown", markInteracted);

    const payload = parsePayloadFromPanel(panel);
    if (payload) {
      renderPayload(payload);
      syncUrl(panel);
    } else {
      refresh(panel, panelId).catch((err) => renderError(err));
    }

    const controlsToWatch = panel.querySelectorAll("[data-control], [data-tag-filter]");
    controlsToWatch.forEach((element) => {
      element.addEventListener("change", (event) => {
        if (!allowAutoRefresh || !userInteractedWithControls || !event.isTrusted) {
          return;
        }
        setCompareControlState(panel);
        scheduleRefresh(panel, panelId);
      });
    });

    setCompareControlState(panel);

    panel.querySelector("[data-action='refresh']")?.addEventListener("click", async () => {
      try {
        await refresh(panel, panelId);
      } catch (err) {
        renderError(err);
      }
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

  document.body.addEventListener("click", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLElement)) {
      return;
    }
    const button = target.closest("[data-metric-item='1']");
    if (button instanceof HTMLElement) {
      clearLive();
      document
        .querySelectorAll("[data-metric-item='1']")
        .forEach((el) => el.classList.remove("active"));
      button.classList.add("active");
    }
  });

  document.body.addEventListener("htmx:afterSwap", (event) => {
    const target = event.detail.target;
    if (!(target instanceof HTMLElement) || target.id !== "metric-panel") {
      return;
    }

    clearLive();
    clearCharts();
    if (controlDebounceTimer) {
      window.clearTimeout(controlDebounceTimer);
      controlDebounceTimer = null;
    }

    const panel = panelRoot();
    if (panel) {
      const activeMetric = panel.dataset.metric;
      if (activeMetric) {
        document
          .querySelectorAll("[data-metric-item='1']")
          .forEach((el) => el.classList.remove("active"));
        document.querySelectorAll("[data-metric-item='1']").forEach((el) => {
          if (!(el instanceof HTMLElement)) {
            return;
          }
          if (el.dataset.metricName === activeMetric) {
            el.classList.add("active");
          }
        });
      }

      bindPanel(panel);
      setLiveButtonState(panel);
    }
  });

  document.addEventListener("DOMContentLoaded", () => {
    initThemeToggle();

    const panel = panelRoot();
    if (panel) {
      bindPanel(panel);
      setLiveButtonState(panel);
    }
  });
})();
