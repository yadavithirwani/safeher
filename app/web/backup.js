/* Plan B is a separate, uncertainty-aware access metric, not a safety guarantee. */
"use strict";
function backupSummary(coverage) {
  if (!coverage) return "";
  if (coverage.max_walk_min == null) return "Plan B: some stretches have no mapped option within 15 min";
  return `Plan B: longest estimated walk to a help point ~${Math.ceil(coverage.max_walk_min)} min`;
}

function renderBackup(route) {
  const coverage = route.plan_b;
  if (!coverage) return "";
  const point = coverage.worst_help_point;
  return `<section class="backup" aria-label="Plan B coverage">
    <h3>Plan B coverage</h3>
    <p><strong>${esc(backupSummary(coverage).replace("Plan B: ", ""))}</strong></p>
    <p>${Math.round(coverage.within_threshold_share * 100)}% of sampled route length is within an estimated ${coverage.threshold_min}-minute walk of a potentially available help point.</p>
    ${coverage.unavailable_length_m ? `<p>${fmtDist(coverage.unavailable_length_m)} has no mapped walking option within ${coverage.search_limit_min} minutes. This means limited mapped coverage—not that help does not exist.</p>` : ""}
    ${point ? `<p>Nearest mapped option at the least-covered measured point: ${esc(point.name || KIND_STYLE[point.kind]?.label || point.kind)}. ${point.hours_assumed ? "Opening hours are assumed, not verified." : "Hours indicate open; availability is not verified."}</p>
      <button class="btn" type="button" data-backup-worst>Show least-covered measured point</button>` : ""}
    ${coverage.gaps.length ? `<h4>Stretches beyond ${coverage.threshold_min} minutes or without a mapped option</h4>
      <ul class="list">${coverage.gaps.map((gap, index) => `<li><button type="button" data-backup-gap="${index}">
        <div><b>${fmtDist(gap.length_m)} stretch</b><small>${gap.unavailable ? "Includes missing mapped coverage" : `Estimated walk exceeds ${coverage.threshold_min} min`}</small></div>
      </button></li>`).join("")}</ul>` : ""}
    <p class="muted small">At least ${coverage.eligible_places_min} eligible mapped places across the area at sampled times; up to ${coverage.assumed_hours_places_max} use assumed hours. These are area-wide counts, not places passed by this route.</p>
    <details><summary>Method and limitations</summary><p>${esc(coverage.limitations)}</p></details>
  </section>`;
}

function bindBackup() {
  document.querySelector("[data-backup-worst]")?.addEventListener("click", () => {
    const coverage = state.plan.routes[state.selected].plan_b;
    flyToPoint(...coverage.worst_point, "Least-covered measured point (estimate)");
  });
  document.querySelectorAll("[data-backup-gap]").forEach((button) => button.addEventListener("click", () => {
    const gap = state.plan.routes[state.selected].plan_b.gaps[Number(button.dataset.backupGap)];
    map.getSource("weak").setData({ type: "FeatureCollection", features: [{ type: "Feature", properties: {},
      geometry: { type: "LineString", coordinates: gap.geometry.map(([lat, lon]) => [lon, lat]) } }] });
    fitTo(gap.geometry);
    toast("Plan B gap: limited access estimate, not a safety classification.");
  }));
}
