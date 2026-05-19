## Vendored frontend dependencies

These files are checked in so the app does not need to reach out to a public CDN at runtime. To refresh them:

```sh
curl -fsSL -o htmx.min.js https://unpkg.com/htmx.org@1.9.12/dist/htmx.min.js
curl -fsSL -o chart.umd.min.js https://cdn.jsdelivr.net/npm/chart.js@4.4.3/dist/chart.umd.min.js
curl -fsSL -o chartjs-plugin-zoom.min.js https://cdn.jsdelivr.net/npm/chartjs-plugin-zoom@2.0.1/dist/chartjs-plugin-zoom.min.js
```

Update the version numbers in this README when you bump.

Licenses: htmx (BSD 2-Clause), Chart.js (MIT), chartjs-plugin-zoom (MIT).
