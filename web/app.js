const svgNS = "http://www.w3.org/2000/svg";

function svgElement(tag, attributes) {
  const element = document.createElementNS(svgNS, tag);
  for (const [key, value] of Object.entries(attributes)) element.setAttribute(key, String(value));
  return element;
}

function node(tag, text, className) {
  const element = document.createElement(tag);
  element.textContent = text;
  if (className) element.className = className;
  return element;
}

function severity(value, { warning, critical }) {
  return value >= critical ? "critical" : value >= warning ? "warning" : "normal";
}

function clock(unixSeconds) {
  return new Date(unixSeconds * 1000).toISOString().slice(11, 19) + " UTC";
}

function number(value) {
  return Number.isFinite(value) ? new Intl.NumberFormat("en-US", { maximumSignificantDigits: 4 }).format(value) : "—";
}

function drawChart(caseData, service) {
  const svg = document.querySelector("#signal-chart");
  svg.querySelectorAll(".plot").forEach((element) => element.remove());
  const values = service.values;
  const finite = values.filter(Number.isFinite);
  if (!finite.length) return;
  const min = Math.min(...finite);
  const span = Math.max(Math.max(...finite) - min, Math.abs(min) * 0.01, 1e-9);
  const x = (index) => 24 + (index * 852) / (values.length - 1);
  const y = (value) => 174 - ((value - min) / span) * 124;
  const plot = svgElement("g", { class: "plot" });
  const style = getComputedStyle(document.documentElement);
  const colors = Object.fromEntries(["normal", "warning", "critical"].map((level) => [level, style.getPropertyValue(`--${level}`).trim()]));
  const colorFor = (value) => colors[severity(value, service.thresholds)];
  const gradient = svgElement("linearGradient", { id: "signal-severity", gradientUnits: "userSpaceOnUse", x1: 24, x2: 876, y1: 0, y2: 0 });
  service.severityScores.forEach((score, index) => {
    if (Number.isFinite(score)) gradient.append(svgElement("stop", { offset: `${(index / (values.length - 1)) * 100}%`, "stop-color": colorFor(score) }));
  });
  const defs = svgElement("defs", {});
  defs.append(gradient);
  plot.append(defs);
  for (const gridY of [50, 112, 174]) plot.append(svgElement("line", { x1: 24, x2: 876, y1: gridY, y2: gridY, stroke: "#303637", "stroke-width": 1 }));
  const onsetX = x(caseData.onsetIndex);
  plot.append(svgElement("line", { x1: onsetX, x2: onsetX, y1: 24, y2: 183, stroke: colors.critical, "stroke-width": 1, "stroke-dasharray": "5 5" }));
  let segment = [];
  const flush = () => {
    if (segment.length > 1) plot.append(svgElement("polyline", { points: segment.join(" "), fill: "none", stroke: "url(#signal-severity)", "stroke-width": 3, "stroke-linejoin": "round", "stroke-linecap": "round" }));
    segment = [];
  };
  values.forEach((value, index) => {
    const score = service.severityScores[index];
    if (!Number.isFinite(value) || !Number.isFinite(score)) { flush(); return; }
    segment.push(`${x(index)},${y(value)}`);
    plot.append(svgElement("circle", { cx: x(index), cy: y(value), r: index === caseData.onsetIndex ? 4.5 : 2, fill: colorFor(score) }));
  });
  flush();
  svg.append(plot);
  document.querySelector("#chart-title").textContent = `${service.metric} around fault injection`;
  document.querySelector("#chart-description").textContent = `Recorded ${service.metric} values from ${clock(caseData.time[0])} to ${clock(caseData.time.at(-1))}. Fault injection at ${clock(caseData.onset)}. Colour indicates absolute deviation from the preceding five-minute baseline: green below 3 robust standard deviations, yellow from 3 to below 6, red at 6 or above. Gaps are missing readings.`;
  document.querySelector("#chart-thresholds").textContent = "SOURCE VALUES, UNCONVERTED  ·  GREEN < 3σ  ·  YELLOW 3–<6σ  ·  RED ≥ 6σ";
}

function selectService(caseData, service) {
  document.querySelectorAll(".service").forEach((button) => {
    const selected = button.dataset.service === service.id;
    button.classList.toggle("selected", selected);
    button.setAttribute("aria-pressed", String(selected));
  });
  document.querySelector("#selected-name").textContent = service.name;
  const last = service.values.at(-1);
  const score = service.severityScores.at(-1);
  const latest = document.querySelector("#latest-reading");
  latest.textContent = number(last);
  latest.style.color = Number.isFinite(score) ? getComputedStyle(document.documentElement).getPropertyValue(`--${severity(score, service.thresholds)}`).trim() : "";
  const badge = document.querySelector("#service-role");
  badge.className = `badge ${service.status}`;
  badge.textContent = `METRIC ${service.status.toUpperCase()}`;
  const onsetText = service.firstAnomalySeconds === null ? "No sustained critical deviation in the first five minutes after injection." : `First sustained critical deviation at +${service.firstAnomalySeconds} seconds.`;
  document.querySelector("#evidence-copy").textContent = `${service.metric}: ${onsetText} Peak deviation: ${number(service.peakDeviation)} robust σ. Hybrid rank #${service.fusionRank}; model #${service.modelRank}; BARO #${service.baroRank}; earliest anomaly #${service.baselineRank}.`;
  document.querySelector("#legend-name").textContent = service.metric;
  drawChart(caseData, service);
}

function renderCase(caseData) {
  const select = document.querySelector("#case-select");
  select.value = caseData.id;
  const mode = document.querySelector("#ranking-mode").value;
  const rank = `${mode}Rank`;
  const ordered = [...caseData.services].sort((a, b) => a[rank] - b[rank]);
  document.querySelector("#ranking-caption").textContent = { fusion: "Hybrid: model + BARO", model: "Held-out model", baro: "Published BARO" }[mode];
  document.querySelector("#hero-title").textContent = `${caseData.systemName} incident`;
  document.querySelector("#hero-copy").textContent = `${caseData.faultDescription} · ${caseData.services.length} metric-bearing services · held-out system: ${caseData.systemName}`;
  document.querySelector("#hero-onset").textContent = `FAULT INJECTION ${new Date(caseData.onset * 1000).toISOString().replace("T", " ").slice(0, 19)} UTC`;
  document.querySelector("#start-time").textContent = clock(caseData.time[0]);
  document.querySelector("#onset-time").textContent = clock(caseData.onset);
  document.querySelector("#end-time").textContent = clock(caseData.time.at(-1));
  const map = document.querySelector("#service-map");
  map.replaceChildren();
  for (const service of ordered) {
    const button = node("button", "", "service");
    button.type = "button";
    button.dataset.service = service.id;
    button.setAttribute("aria-pressed", "false");
    button.setAttribute("aria-label", `${service.name}, ${mode} rank ${service[rank]}, latest metric ${service.status}`);
    const top = node("span", "", "service-top");
    top.append(node("span", `#${service[rank]}  ${service.name}`, "service-name"), node("span", service.status.toUpperCase(), `service-state ${service.status}`));
    button.append(top, node("span", service.metric, "service-sub"));
    button.addEventListener("click", () => selectService(caseData, service));
    map.append(button);
  }
  map.scrollTop = 0;
  selectService(caseData, ordered[0]);
  const hypotheses = document.querySelector("#hypotheses");
  hypotheses.replaceChildren();
  for (const service of ordered.slice(0, 5)) {
    const item = node("li", "", "hypothesis");
    item.append(node("strong", service.name), node("span", `model #${service.modelRank} · BARO #${service.baroRank}`));
    hypotheses.append(item);
  }
  const truth = document.querySelector("#ground-truth");
  truth.textContent = "";
  const reveal = document.querySelector("#reveal-truth");
  reveal.hidden = false;
  reveal.onclick = () => {
    truth.textContent = `${caseData.groundTruth} · hybrid #${caseData.truthRanks.fusion} · model #${caseData.truthRanks.model} · BARO #${caseData.truthRanks.baro} · anomaly #${caseData.truthRanks.baseline} · case ${caseData.id}`;
    reveal.hidden = true;
  };
}

function table(headers, rows) {
  const element = node("table", "", "results-table");
  const head = node("thead", "");
  const headRow = node("tr", "");
  headers.forEach((label) => headRow.append(node("th", label)));
  head.append(headRow);
  const body = node("tbody", "");
  rows.forEach((values) => {
    const row = node("tr", "");
    values.forEach((value) => row.append(node("td", String(value))));
    body.append(row);
  });
  element.append(head, body);
  return element;
}

function renderEvaluation(bundle, repeat, validation) {
  const results = document.querySelector("#evaluation");
  const percent = (value) => `${(value * 100).toFixed(1)}%`;
  const methodRows = (summary) => [
    ["Earliest anomaly", percent(summary.baseline.top1), percent(summary.baseline.top3), summary.baseline.mrr.toFixed(3)],
    ["BARO (published)", percent(summary.baro.top1), percent(summary.baro.top3), summary.baro.mrr.toFixed(3)],
    ["Trained model", percent(summary.model.top1), percent(summary.model.top3), summary.model.mrr.toFixed(3)],
    ["Hybrid (model + BARO)", percent(summary.fusion.top1), percent(summary.fusion.top3), summary.fusion.mrr.toFixed(3)],
  ];
  results.append(node("h4", "Development · first 75 cases"));
  results.append(table(["Method", "Top 1", "Top 3", "MRR"], methodRows(bundle.evaluation)));
  results.append(node("p", "The hybrid rule was selected using the first two repetitions. Their scores describe development performance, not independent validation.", "eval-note"));
  results.append(node("h4", "Top-1 by held-out system"));
  results.append(table(["System", "Anomaly", "BARO", "Model", "Hybrid"], Object.entries(bundle.bySystem).map(([system, result]) => [system.toUpperCase(), percent(result.baseline.top1), percent(result.baro.top1), percent(result.model.top1), percent(result.fusion.top1)])));
  results.append(node("h4", "Top-1 by injected fault"));
  results.append(table(["Fault", "Anomaly", "BARO", "Model", "Hybrid"], Object.entries(bundle.byFault).map(([fault, result]) => [fault.toUpperCase(), percent(result.baseline.top1), percent(result.baro.top1), percent(result.model.top1), percent(result.fusion.top1)])));
  results.append(node("h4", "Development · second 75 cases"));
  results.append(table(["Method", "Top 1", "Top 3", "MRR"], methodRows(repeat.evaluation)));
  results.append(node("h4", "Validation · later 223 cases"));
  results.append(table(["Method", "Top 1", "Top 3", "MRR"], methodRows(validation.evaluation)));
  const difference = validation.evaluation.fusionVsBaroTop1;
  results.append(node("p", `Hybrid–BARO top-1 difference: ${percent(difference.difference)} (paired 95% interval ${percent(difference.ci95[0])}–${percent(difference.ci95[1])}; resampled by service/fault pair). The hybrid includes BARO, and all cases come from the same three systems.`, "eval-note"));
  document.querySelector("#case-count").textContent = String(bundle.cases.length);
  document.querySelector("#eval-count").textContent = `${bundle.cases.length + repeat.cases.length + validation.cases.length} CASES`;
}

async function main() {
  try {
    const [response, repeatResponse, validationResponse] = await Promise.all([fetch("benchmark.json"), fetch("sensitivity.json"), fetch("validation.json")]);
    if (!response.ok || !repeatResponse.ok || !validationResponse.ok) throw new Error(`Benchmark data request failed: ${response.status}/${repeatResponse.status}/${validationResponse.status}`);
    const [bundle, repeat, validation] = await Promise.all([response.json(), repeatResponse.json(), validationResponse.json()]);
    if (bundle.kind !== "real-benchmark" || !Array.isArray(bundle.cases) || !bundle.cases.length) throw new Error("Invalid benchmark data");
    if (repeat.kind !== "real-benchmark" || repeat.cases?.length !== 75) throw new Error("Invalid repetition data");
    if (validation.kind !== "real-benchmark" || validation.cases?.length !== 223) throw new Error("Invalid validation data");
    const select = document.querySelector("#case-select");
    const grouped = Object.groupBy(bundle.cases, (caseData) => caseData.systemName);
    for (const [system, cases] of Object.entries(grouped)) {
      const group = node("optgroup", "");
      group.label = system;
      cases.forEach((caseData, index) => {
        const option = node("option", `${caseData.fault.toUpperCase()} · Case ${String(index + 1).padStart(2, "0")}`);
        option.value = caseData.id;
        group.append(option);
      });
      select.append(group);
    }
    select.addEventListener("change", () => renderCase(bundle.cases.find((caseData) => caseData.id === select.value)));
    document.querySelector("#ranking-mode").addEventListener("change", () => renderCase(bundle.cases.find((caseData) => caseData.id === select.value)));
    renderEvaluation(bundle, repeat, validation);
    renderCase(bundle.cases[0]);
  } catch (error) {
    document.querySelector("#hero-title").textContent = "Benchmark unavailable";
    document.querySelector("#hero-copy").textContent = `${error.message}. Run python benchmark.py all to generate the data.`;
  }
}

main();
