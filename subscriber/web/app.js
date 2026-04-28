const metricDefs = [
  { key: "sicaklik", label: "Sıcaklık", unit: "°C", chartId: "sicaklikChart" },
  { key: "nem", label: "Nem", unit: "%", chartId: "nemChart" },
  { key: "isik", label: "Işık", unit: "lux", chartId: "isikChart" },
  { key: "pm25", label: "PM2.5", unit: "ug/m3", chartId: "pm25Chart" },
  { key: "mq135_ppm_est", label: "MQ-135", unit: "ppm", chartId: "mq135Chart" },
  { key: "mq7_ppm_est", label: "MQ-7", unit: "ppm", chartId: "mq7Chart" },
  { key: "mq2_ppm_est", label: "MQ-2", unit: "ppm", chartId: "mq2Chart" },
  { key: "fan_pwm", label: "Fan PWM", unit: "", chartId: "fanPwmChart" },
  { key: "decision_score", label: "Karar Skoru", unit: "", chartId: "scoreChart" },
  { key: "trend_score", label: "Trend Skoru", unit: "", chartId: "trendScoreChart" }
];

const charts = {};

function makeCard(title, value, sub = "") {
  return `
    <article class="card">
      <h3>${title}</h3>
      <div class="value">${value}</div>
      ${sub ? `<div>${sub}</div>` : ""}
    </article>
  `;
}

function makeStatsCard(metric, stats) {
  return `
    <article class="stat-card">
      <h3>${metric.label}</h3>
      <div class="stat-list">
        <div>Min</div><div>${stats.min.toFixed(2)}</div>
        <div>Maks</div><div>${stats.max.toFixed(2)}</div>
        <div>Ortalama</div><div>${stats.avg.toFixed(2)}</div>
        <div>Varyans</div><div>${stats.variance.toFixed(2)}</div>
      </div>
    </article>
  `;
}

function buildChart(metric) {
  const ctx = document.getElementById(metric.chartId);
  charts[metric.key] = new Chart(ctx, {
    type: "line",
    data: {
      labels: [],
      datasets: [
        {
          label: `${metric.label} (${metric.unit})`,
          data: [],
          borderColor: "#58a6ff",
          backgroundColor: "rgba(88,166,255,0.2)",
          borderWidth: 2,
          pointRadius: 0,
          tension: 0.2
        }
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { labels: { color: "#e6edf3" } } },
      scales: {
        x: { ticks: { color: "#8b949e" } },
        y: { ticks: { color: "#8b949e" } }
      }
    }
  });
}

function updateCharts(history) {
  const labels = history.map((r) => new Date(r.timestamp).toLocaleTimeString("tr-TR"));
  for (const metric of metricDefs) {
    const chart = charts[metric.key];
    const values = history.map((r) => Number(r.values[metric.key] ?? 0));
    chart.data.labels = labels;
    chart.data.datasets[0].data = values;
    chart.update("none");
  }
}

async function fetchJson(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

async function refresh() {
  try {
    const [latestRes, historyRes, statsRes] = await Promise.all([
      fetchJson("/api/latest"),
      fetchJson("/api/history?limit=180"),
      fetchJson("/api/stats?limit=500")
    ]);

    const cards = document.getElementById("liveCards");
    if (latestRes.status !== "ok") {
      cards.innerHTML = makeCard("Durum", "Veri bekleniyor");
      return;
    }

    const d = latestRes.data.values;
    const fanChip = d.fan_on
      ? `<span class="chip ok">Fan Açık (${d.fan_pwm})</span>`
      : `<span class="chip warn">Fan Kapalı</span>`;
    const alarmChip = d.buzzer_on
      ? `<span class="chip warn">Gaz Alarmı Aktif</span>`
      : `<span class="chip ok">Alarm Pasif</span>`;

    cards.innerHTML =
      makeCard("Sıcaklık", `${Number(d.sicaklik).toFixed(1)} °C`) +
      makeCard("Nem", `${Number(d.nem).toFixed(1)} %`) +
      makeCard("Işık", `${Number(d.isik).toFixed(0)} lux`) +
      makeCard("PM2.5", `${Number(d.pm25).toFixed(1)} ug/m3`) +
      makeCard("MQ-135", `${Number(d.mq135_ppm_est).toFixed(2)} ppm`) +
      makeCard("MQ-7 (CO)", `${Number(d.mq7_ppm_est).toFixed(2)} ppm`) +
      makeCard("MQ-2 (Gaz/Duman)", `${Number(d.mq2_ppm_est).toFixed(2)} ppm`) +
      makeCard("Karar Modu", d.decision_mode, fanChip) +
      makeCard("Karar Skoru", Number(d.decision_score).toFixed(3), alarmChip) +
      makeCard("Trend Skoru", Number(d.trend_score).toFixed(3));

    updateCharts(historyRes.data || []);

    const statsGrid = document.getElementById("statsGrid");
    if (statsRes.status !== "ok") {
      statsGrid.innerHTML = "";
      return;
    }
    statsGrid.innerHTML = metricDefs
      .filter((m) => statsRes.metrics[m.key])
      .map((m) => makeStatsCard(m, statsRes.metrics[m.key]))
      .join("");
  } catch (err) {
    console.error(err);
  }
}

for (const metric of metricDefs) {
  buildChart(metric);
}
refresh();
setInterval(refresh, 3000);
