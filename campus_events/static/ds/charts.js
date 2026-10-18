/* 数値はサーバーの集計結果を使用。外部CDN・外部へのデータ送信は不要。 */
"use strict";
(() => {
  const source = document.getElementById("analytics-data");
  if (!source) return;
  const report = JSON.parse(source.textContent);
  const number = new Intl.NumberFormat("ja-JP", { maximumFractionDigits: 2 });
  const labels = {
    registration_count: "サイト内申込", average_registrations: "1イベント平均",
    event_count: "開催件数", favorite_count: "お気に入り", view_count: "閲覧記録",
  };
  function addText(parent, tag, className, value) {
    const node = document.createElement(tag);
    node.className = className;
    node.textContent = value;
    parent.append(node);
    return node;
  }
  function draw(kind, metric) {
    const container = document.querySelector(`[data-chart="${kind}"]`);
    if (!container) return;
    let rows = kind === "ranking"
      ? report.ranking.filter(row => row.registration_mode === "site").slice(0, 10)
        .map(row => ({ ...row, label: row.title, registration_count: row.registered }))
      : [...report[kind]];
    if (kind === "categories") {
      rows = rows.filter(row => row.event_count > 0)
        .sort((a, b) => (b[metric] ?? -1) - (a[metric] ?? -1));
    }
    container.replaceChildren();
    if (!rows.length) {
      addText(container, "p", "chart-empty", "集計対象のイベントがありません。");
      return;
    }
    const maximum = Math.max(1, ...rows.map(row => row[metric] ?? 0));
    rows.forEach(row => {
      const value = row[metric];
      const line = document.createElement("div");
      line.className = "bar-row";
      const label = addText(line, "span", "bar-label", row.label);
      if (kind !== "ranking") {
        addText(label, "small", "bar-context", `開催 ${row.event_count}件 · サイト内受付 ${row.site_event_count}件`);
      }
      const track = document.createElement("div");
      track.className = value === null ? "bar-track is-missing" : "bar-track";
      track.setAttribute("aria-hidden", "true");
      if (value !== null) {
        const fill = document.createElement("div");
        fill.className = "bar-fill";
        fill.style.width = `${Math.max(0, value) / maximum * 100}%`;
        track.append(fill);
      }
      line.append(track);
      const formatted = value === null ? "対象なし" : `${number.format(value)}件`;
      addText(line, "span", "bar-value", formatted);
      line.title = `${row.label} / ${labels[metric]}: ${formatted}`;
      container.append(line);
    });
  }
  document.querySelectorAll("[data-metric-for]").forEach(select => {
    const update = () => draw(select.dataset.metricFor, select.value);
    select.addEventListener("change", update);
    update();
  });
  draw("ranking", "registration_count");
})();
