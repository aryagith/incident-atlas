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

function exportReport(caseData) {
  const cloud = caseData.kind === "cloud";
  const ordered = [...caseData.services].sort((a, b) => cloud ? a.evidenceRank - b.evidenceRank : a.fusionRank - b.fusionRank);
  const lines = [
    "# Incident Atlas investigation report",
    "",
    `Incident: ${caseData.systemName}`,
    `Fault onset: ${new Date(caseData.onset * 1000).toISOString()}`,
    `Source: ${cloud ? caseData.source : caseData.kind === "uploaded" ? "Imported metrics CSV" : `RCAEval RE1 case ${caseData.id}`}`,
    ...(cloud ? [`Observed cadence: about ${caseData.cadenceSeconds} seconds`, "Method: native-cadence robust deviation; two consecutive observations for a sustained change"] : []),
    `Services analyzed: ${caseData.services.length}`,
    "",
    "## Ranked service candidates",
    "",
    ...ordered.slice(0, 5).map((service) => cloud
      ? `${service.evidenceRank}. ${service.name} — ${service.metric}; ${service.insufficientPostSamples ? `${service.missingMetrics}/${service.metricCount} metric streams have fewer than five post-onset readings` : `first sustained deviation ${service.firstAnomalySeconds === null ? "not observed" : `+${service.firstAnomalySeconds}s`}; peak ${number(service.peakDeviation)} robust σ; ${service.triggeredMetrics}/${service.metricCount} metrics triggered`}`
      : `${service.fusionRank}. ${service.name} — model #${service.modelRank}, BARO #${service.baroRank}; ${service.metric}; first sustained deviation ${service.firstAnomalySeconds === null ? "not observed" : `+${service.firstAnomalySeconds}s`}; peak ${number(service.peakDeviation)} robust σ`),
    "",
    ...(caseData.groundTruth ? [`Annotated root service: ${caseData.groundTruth}`, ""] : []),
    cloud ? "This evidence ranking has not been validated as a root-cause model on cloud incidents. Missing telemetry may reflect stopped tasks or monitoring gaps. Verify in your cloud console." : "The hybrid combines model and BARO ranks. Its scores are hypotheses, not calibrated probabilities or proof of causality.",
    ...(caseData.signalWarning ? [caseData.signalWarning] : []),
  ];
  const file = new Blob([lines.join("\n") + "\n"], { type: "text/markdown" });
  const url = URL.createObjectURL(file);
  const link = document.createElement("a");
  link.href = url;
  link.download = `incident-atlas-${caseData.systemName.toLowerCase().replace(/[^a-z0-9]+/g, "-").slice(0, 40)}.md`;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function drawChart(caseData, service) {
  const svg = document.querySelector("#signal-chart");
  svg.querySelectorAll(".plot").forEach((element) => element.remove());
  const values = service.values;
  const times = service.times || caseData.time;
  const finite = values.filter(Number.isFinite);
  if (!finite.length) return;
  const min = Math.min(...finite);
  const span = Math.max(Math.max(...finite) - min, Math.abs(min) * 0.01, 1e-9);
  const rangeStart = caseData.kind === "cloud" ? caseData.time[0] : times[0];
  const rangeEnd = caseData.kind === "cloud" ? caseData.time.at(-1) : times.at(-1);
  const range = Math.max(rangeEnd - rangeStart, 1);
  const x = (index) => 24 + ((times[index] - rangeStart) * 852) / range;
  const y = (value) => 174 - ((value - min) / span) * 124;
  const plot = svgElement("g", { class: "plot" });
  const style = getComputedStyle(document.documentElement);
  const colors = Object.fromEntries(["normal", "warning", "critical"].map((level) => [level, style.getPropertyValue(`--${level}`).trim()]));
  const colorFor = (value) => colors[severity(value, service.thresholds)];
  const gradient = svgElement("linearGradient", { id: "signal-severity", gradientUnits: "userSpaceOnUse", x1: 24, x2: 876, y1: 0, y2: 0 });
  service.severityScores.forEach((score, index) => {
    if (Number.isFinite(score)) gradient.append(svgElement("stop", { offset: `${((times[index] - rangeStart) / range) * 100}%`, "stop-color": colorFor(score) }));
  });
  const defs = svgElement("defs", {});
  defs.append(gradient);
  plot.append(defs);
  for (const gridY of [50, 112, 174]) plot.append(svgElement("line", { x1: 24, x2: 876, y1: gridY, y2: gridY, stroke: "#303637", "stroke-width": 1 }));
  const onsetX = 24 + ((caseData.onset - rangeStart) * 852) / range;
  plot.append(svgElement("line", { x1: onsetX, x2: onsetX, y1: 24, y2: 183, stroke: colors.critical, "stroke-width": 1, "stroke-dasharray": "5 5" }));
  let segment = [];
  const flush = () => {
    if (segment.length > 1) plot.append(svgElement("polyline", { points: segment.join(" "), fill: "none", stroke: "url(#signal-severity)", "stroke-width": 3, "stroke-linejoin": "round", "stroke-linecap": "round" }));
    segment = [];
  };
  values.forEach((value, index) => {
    const score = service.severityScores[index];
    if (!Number.isFinite(value) || !Number.isFinite(score)) { flush(); return; }
    if (caseData.kind === "cloud" && index && times[index] - times[index - 1] > 2.5 * caseData.cadenceSeconds) flush();
    segment.push(`${x(index)},${y(value)}`);
    plot.append(svgElement("circle", { cx: x(index), cy: y(value), r: times[index] === caseData.onset ? 4.5 : 2, fill: colorFor(score) }));
  });
  flush();
  if (service.insufficientPostSamples) {
    const annotation = svgElement("text", { x: Math.min(onsetX + 14, 650), y: 42, fill: colors.critical, "font-size": 12, "font-family": "ui-monospace, monospace" });
    annotation.textContent = "INSUFFICIENT POST-ONSET READINGS";
    plot.append(annotation);
  }
  svg.append(plot);
  const onsetLabel = caseData.kind === "uploaded" || caseData.kind === "cloud" ? "Incident onset" : "Fault injection";
  document.querySelector("#chart-title").textContent = `${service.metric} around ${onsetLabel.toLowerCase()}`;
  document.querySelector("#chart-description").textContent = `Recorded ${service.metric} values from ${clock(times[0])} to ${clock(times.at(-1))}. ${onsetLabel} at ${clock(caseData.onset)}. Colour indicates absolute deviation from ${caseData.kind === "cloud" ? "pre-onset observations" : "the preceding five-minute baseline"}: green below 3 robust standard deviations, yellow from 3 to below 6, red at 6 or above. Gaps are missing readings.${service.insufficientPostSamples ? " This metric has fewer than five post-onset readings while another service continued reporting." : ""}`;
  document.querySelector("#start-time").textContent = clock(rangeStart);
  document.querySelector("#end-time").textContent = clock(rangeEnd);
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
  latest.textContent = `${number(last)}${service.unit ? ` ${service.unit}` : ""}${service.insufficientPostSamples ? " · last seen" : ""}`;
  latest.style.color = service.insufficientPostSamples ? getComputedStyle(document.documentElement).getPropertyValue("--critical").trim() : Number.isFinite(score) ? getComputedStyle(document.documentElement).getPropertyValue(`--${severity(score, service.thresholds)}`).trim() : "";
  const badge = document.querySelector("#service-role");
  badge.className = `badge ${service.status}`;
  badge.textContent = service.insufficientPostSamples ? "SIGNAL MISSING" : `METRIC ${service.status.toUpperCase()}`;
  const cloud = caseData.kind === "cloud";
  const onsetText = service.firstAnomalySeconds === null ? cloud ? "No two consecutive critical observations after onset." : "No sustained critical deviation in the first five minutes after onset." : `First sustained critical deviation at +${service.firstAnomalySeconds} seconds.`;
  document.querySelector("#evidence-copy").textContent = cloud
    ? service.insufficientPostSamples
      ? `${service.missingMetrics} of ${service.metricCount} metric streams have fewer than five post-onset readings while other services continued. This can indicate stopped tasks or a telemetry gap; verify in CloudWatch and ECS. Evidence rank #${service.evidenceRank}.`
      : `${service.metric}: ${onsetText} Peak deviation: ${number(service.peakDeviation)} robust σ. Evidence rank #${service.evidenceRank}; ${service.triggeredMetrics} of ${service.metricCount} metrics triggered. Observed cadence about ${caseData.cadenceSeconds} seconds.`
    : `${service.metric}: ${onsetText} Peak deviation: ${number(service.peakDeviation)} robust σ. Hybrid rank #${service.fusionRank}; model #${service.modelRank}; BARO #${service.baroRank}; earliest anomaly #${service.baselineRank}.`;
  document.querySelector("#legend-name").textContent = service.metric;
  drawChart(caseData, service);
}

function renderCase(caseData) {
  const select = document.querySelector("#case-select");
  select.value = caseData.id;
  const cloud = caseData.kind === "cloud";
  const uploaded = caseData.kind === "uploaded" || cloud;
  const ranking = document.querySelector("#ranking-mode");
  for (const option of ranking.options) {
    option.hidden = cloud ? option.value !== "evidence" : option.value === "evidence";
    option.disabled = option.hidden;
  }
  if (cloud) ranking.value = "evidence";
  else if (ranking.value === "evidence") ranking.value = "fusion";
  const mode = ranking.value;
  const rank = `${mode}Rank`;
  const ordered = [...caseData.services].sort((a, b) => a[rank] - b[rank]);
  document.querySelector("#ranking-caption").textContent = cloud ? "Observed metric evidence" : { fusion: "Hybrid: model + BARO", model: uploaded ? "Deployed model" : "Held-out model", baro: "Published BARO" }[mode];
  document.querySelector("#hero-eyebrow").textContent = uploaded ? "YOUR DATA / INVESTIGATION" : "RE1 / HELD-OUT INVESTIGATION";
  document.querySelector("#sidebar-eyebrow").textContent = uploaded ? "YOUR DATA / SESSION" : "BENCHMARK / 01";
  document.querySelector("#sidebar-scope").textContent = cloud ? "Cloud service observations are ranked at their original cadence. This is change evidence near onset, not a validated causal model." : uploaded ? "This incident has no annotated root cause. Rankings are candidates based on its metric window; benchmark scores do not validate this result." : "75 selected RE1 cases across three systems. Rankings use metrics only and were evaluated on a system excluded from training.";
  document.querySelector("#hero-title").textContent = uploaded ? caseData.systemName : `${caseData.systemName} incident`;
  document.querySelector("#hero-copy").textContent = cloud ? `${caseData.services.length} services · about ${caseData.cadenceSeconds}s between observations. ${caseData.signalWarning || "Evidence ranking from metric changes around the supplied onset."}` : uploaded ? `${caseData.services.length} metric-bearing services · model trained on ${caseData.modelTrainingCases} labeled benchmark cases. ${caseData.signalWarning || "No annotated root cause is available for this import."}` : `${caseData.faultDescription} · ${caseData.services.length} metric-bearing services · held-out system: ${caseData.systemName}`;
  document.querySelector("#data-badge").textContent = cloud ? "CLOUD SERVICE METRICS" : uploaded ? "IMPORTED METRICS" : "REAL BENCHMARK DATA";
  document.querySelector("#top-badge").textContent = cloud ? "CLOUD METRICS" : "RCAEval · RE1";
  document.querySelector("#top-context").textContent = cloud ? "LOCAL INVESTIGATION" : "METRIC-ONLY STUDY";
  document.querySelector("#source-name").textContent = cloud ? caseData.source : uploaded ? "Imported CSV" : "RCAEval RE1";
  document.querySelector("#source-description").textContent = cloud ? "Original sampling cadence" : uploaded ? "Local metrics · no known answer" : "Metric-only fault injections";
  document.querySelector("#ranking-name").textContent = cloud ? "Evidence ranking" : { fusion: "Hybrid ranker", model: "Trained model", baro: "BARO" }[mode];
  document.querySelector("#ranking-description").textContent = cloud ? "Robust pre-onset deviation" : { fusion: "Equal-weight model + BARO ranks", model: "Trained on RCAEval cases", baro: "Published metric ranker" }[mode];
  document.querySelector("#count-label").textContent = uploaded ? "ANALYZED SERVICES" : "EVALUATED CASES";
  document.querySelector("#case-count").textContent = uploaded ? String(caseData.services.length) : "75";
  document.querySelector("#count-description").textContent = cloud ? "With usable pre-onset observations" : uploaded ? "Ranked from this incident window" : "75 selected system × service × fault pairs";
  document.querySelector(".metrics").setAttribute("aria-label", uploaded ? "Incident summary" : "Benchmark summary");
  document.querySelector("#hero-onset").textContent = `${uploaded ? "INCIDENT ONSET" : "FAULT INJECTION"} ${new Date(caseData.onset * 1000).toISOString().replace("T", " ").slice(0, 19)} UTC`;
  document.querySelector("#chart-onset-label").textContent = uploaded ? "INCIDENT ONSET" : "FAULT INJECTION";
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
    item.append(node("strong", service.name), node("span", cloud ? `${service.metric} · ${service.insufficientPostSamples ? "signal missing" : service.firstAnomalySeconds === null ? "no sustained change" : `+${service.firstAnomalySeconds}s`}` : `model #${service.modelRank} · BARO #${service.baroRank}`));
    hypotheses.append(item);
  }
  const truth = document.querySelector("#ground-truth");
  truth.textContent = "";
  const reveal = document.querySelector("#reveal-truth");
  reveal.hidden = uploaded;
  if (!uploaded) reveal.onclick = () => {
    truth.textContent = `${caseData.groundTruth} · hybrid #${caseData.truthRanks.fusion} · model #${caseData.truthRanks.model} · BARO #${caseData.truthRanks.baro} · anomaly #${caseData.truthRanks.baseline} · case ${caseData.id}`;
    reveal.hidden = true;
  };
  document.querySelector("#download-report").onclick = () => exportReport(caseData);
  document.querySelector("#ranking-footnote").textContent = cloud ? "Ranks prioritize missing service telemetry, then the earliest two consecutive critical observations, affected metric count, and peak deviation. No RCAEval model or BARO score is applied to cloud data. Check whether missing data reflects a stopped task or monitoring gap." : uploaded ? "Hybrid combines the deployable model and BARO ranks. This import is unlabelled; the benchmark scores shown alongside it do not measure accuracy on your data." : "Hybrid combines model and BARO service ranks; it uses BARO as an input. Predictions were saved by the reproducible Python benchmark run.";
  document.querySelector("#evaluation-footnote").textContent = uploaded ? "These tables describe RCAEval fault injections, not the accuracy of this imported incident. The deployed model was retrained on all 373 valid RE1 cases." : "Each case is tested on a system excluded from model training. The first two repetitions were used to choose the hybrid rule; the later 223 cases are its validation set. These subsets are not a full RCAEval score.";
  document.querySelector("#evaluation-panel").hidden = cloud;
  document.querySelector("#lower-grid").classList.toggle("cloud-mode", cloud);
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
    const casesById = new Map(bundle.cases.map((caseData) => [caseData.id, caseData]));
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
    select.addEventListener("change", () => renderCase(casesById.get(select.value)));
    document.querySelector("#ranking-mode").addEventListener("change", () => renderCase(casesById.get(select.value)));
    const panel = document.querySelector("#import-panel");
    const feedback = document.querySelector("#import-feedback");
    const run = document.querySelector("#run-analysis");
    document.querySelector("#open-import").addEventListener("click", () => { panel.hidden = false; panel.scrollIntoView({ behavior: "smooth", block: "start" }); });
    document.querySelector("#close-import").addEventListener("click", () => { panel.hidden = true; });
    async function submitAnalysis(content, onset, name) {
      run.disabled = true;
      feedback.className = "";
      feedback.textContent = "Analyzing metric evidence…";
      try {
        const response = await fetch("api/analyze", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ content, onset, name }) });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || `Analysis failed (${response.status})`);
        const caseData = result.case;
        caseData.modelTrainingCases = result.modelTrainingCases;
        casesById.set(caseData.id, caseData);
        let group = document.querySelector("#imported-cases");
        if (!group) {
          group = node("optgroup", "");
          group.id = "imported-cases";
          group.label = "Your session";
          select.prepend(group);
        }
        let option = [...group.children].find((item) => item.value === caseData.id);
        if (!option) {
          option = node("option", caseData.systemName);
          option.value = caseData.id;
          group.append(option);
        }
        renderCase(caseData);
        feedback.className = "success";
        feedback.textContent = `Analyzed ${result.input.metrics} metrics across ${caseData.services.length} services.`;
        panel.hidden = true;
        document.querySelector(".hero").scrollIntoView({ behavior: "smooth", block: "start" });
      } catch (error) {
        feedback.className = "error";
        feedback.textContent = error.message;
      } finally {
        run.disabled = false;
      }
    }
    document.querySelector("#import-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      const file = document.querySelector("#metrics-file").files[0];
      if (!file) return;
      if (file.size > 4_500_000) { feedback.className = "error"; feedback.textContent = "Choose a file smaller than 4.5 MB."; return; }
      await submitAnalysis(await file.text(), document.querySelector("#incident-onset").value.trim(), document.querySelector("#incident-name").value);
    });
    document.querySelector("#try-example").addEventListener("click", async () => {
      panel.hidden = false;
      try {
        const response = await fetch("example.csv");
        if (!response.ok) throw new Error("Sample CSV unavailable");
        await submitAnalysis(await response.text(), 1685202688, "Sample adservice CPU incident");
      } catch (error) { feedback.className = "error"; feedback.textContent = error.message; }
    });
    renderEvaluation(bundle, repeat, validation);
    renderCase(bundle.cases[0]);
  } catch (error) {
    document.querySelector("#hero-title").textContent = "Benchmark unavailable";
    document.querySelector("#hero-copy").textContent = `${error.message}. Run python benchmark.py all to generate the data.`;
  }
}

main();
