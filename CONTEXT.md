# StatView

Browses Prometheus metrics through a Flask + HTMX UI. Saved views and dashboards let users name a configured graph and revisit it.

## Language

**Metric**:
A Prometheus time-series name (e.g. `http_requests_total`). Identified by name alone; not to be confused with the underlying samples.
_Avoid_: series (a series is name + label set), stat (UI copy only).

**Metric Catalog**:
The set of metric names known to the connected Prometheus, together with their type and available label values. Materialised on demand from Prometheus's metadata and label-values APIs.
_Avoid_: index, metrics list.

**Label Filter**:
A user's choice of `label=value` constraint applied to a metric query. May be **shared** (applies to every selected metric in a view) or **per-metric** (applies only to one). Both kinds round-trip through URL params and saved-view storage.
_Avoid_: selector (PromQL term), tag, dimension.

**Saved View**:
A named, persisted configuration of selected metrics, time window, step, comparison settings, and label filters. Shared across all authorised users.
_Avoid_: query, preset (preset means something else — see below).

**Preset**:
One of the fixed comparison time-windows (e.g. last hour vs. previous hour) rendered alongside the primary query for every saved view. Defined in `app.config.STANDARD_PRESETS`.
_Avoid_: standard view, default.

**Dashboard**:
An ordered collection of saved views shown together on one page.
_Avoid_: board, panel group.

## Relationships

- A **Saved View** selects one or more **Metrics** and pins **Label Filters** to them.
- A **Label Filter** is either shared (applies across all **Metrics** in the **Saved View**) or per-metric.
- Validating a **Label Filter** requires the **Metric Catalog** — only labels and values Prometheus reports are kept.
- A **Dashboard** orders one or more **Saved Views**.
- Every **Saved View** render includes its primary query plus the configured **Presets**.

## Example dialogue

> **Dev:** "If a **Saved View** has the shared **Label Filter** `env=prod` and the user adds a new **Metric** to it, does the filter apply?"
> **Domain expert:** "Yes — that's the whole point of shared. Shared **Label Filters** stay in the **Saved View** and get merged into each **Metric**'s query at render time. If the new **Metric** doesn't have an `env` label in the **Metric Catalog**, the filter is silently dropped for that metric only — the **Saved View** keeps the user's intent."
