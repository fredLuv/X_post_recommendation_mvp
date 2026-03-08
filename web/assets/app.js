const state = {
  audienceId: "",
  recommendations: [],
  evidenceCollapsed: false,
};

const audienceInput = document.querySelector("#audience-id");
const refreshButton = document.querySelector("#refresh-button");
const toggleEvidenceButton = document.querySelector("#toggle-evidence-button");
const topicFilterInput = document.querySelector("#topic-filter");
const sortModeSelect = document.querySelector("#sort-mode");
const recommendationGrid = document.querySelector("#recommendation-grid");
const summaryStrip = document.querySelector("#summary-strip");
const emptyState = document.querySelector("#empty-state");
const statusPill = document.querySelector("#status-pill");
const renderedCount = document.querySelector("#rendered-count");
const template = document.querySelector("#recommendation-card-template");

function setStatus(label) {
  statusPill.textContent = label;
}

function formatDate(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString([], {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

function buildSummaryStats(items) {
  const topicSet = new Set(items.map((item) => item.topic));
  const evidenceCount = items.reduce((sum, item) => sum + item.evidence.length, 0);
  const hookCount = items.reduce((sum, item) => sum + item.draft_hooks.length, 0);

  return [
    { label: "Topics", value: topicSet.size },
    { label: "Recommendations", value: items.length },
    { label: "Evidence bullets", value: evidenceCount },
    { label: "Draft hooks", value: hookCount },
  ];
}

function renderSummary(items) {
  summaryStrip.replaceChildren();
  buildSummaryStats(items).forEach((stat) => {
    const block = document.createElement("div");
    block.className = "summary-stat";
    block.innerHTML = `<span class="label">${stat.label}</span><strong>${stat.value}</strong>`;
    summaryStrip.append(block);
  });
}

function renderRecommendations(items) {
  recommendationGrid.replaceChildren();
  emptyState.classList.toggle("hidden", items.length !== 0);
  renderedCount.textContent = `${items.length} card${items.length === 1 ? "" : "s"}`;

  items.forEach((item) => {
    const fragment = template.content.cloneNode(true);
    const card = fragment.querySelector(".recommendation-card");
    fragment.querySelector(".recommendation-card__topic").textContent = item.topic;
    fragment.querySelector(".recommendation-card__title").textContent = item.recommendation;
    fragment.querySelector(".recommendation-card__time").textContent = formatDate(item.generated_at);
    fragment.querySelector(".recommendation-card__why-now").textContent = item.why_now;
    fragment.querySelector(".suggested-angle").textContent = item.suggested_angle;
    fragment.querySelector(".format").textContent = item.format;
    fragment.querySelector(".audience-fit").textContent = item.audience_fit;

    const evidenceList = fragment.querySelector(".evidence-list");
    item.evidence.forEach((evidence) => {
      const li = document.createElement("li");
      li.textContent = evidence.evidence_text;
      evidenceList.append(li);
    });

    const hookList = fragment.querySelector(".hook-list");
    item.draft_hooks.forEach((hook) => {
      const li = document.createElement("li");
      li.textContent = hook;
      hookList.append(li);
    });

    const riskList = fragment.querySelector(".risk-list");
    item.risks.forEach((risk) => {
      const li = document.createElement("li");
      li.textContent = risk;
      riskList.append(li);
    });

    const evidenceSection = fragment.querySelector(".recommendation-card__evidence");
    const sectionToggle = fragment.querySelector(".section-toggle");
    const setEvidenceVisibility = (collapsed) => {
      evidenceList.classList.toggle("hidden", collapsed);
      sectionToggle.textContent = collapsed ? "Show" : "Hide";
    };
    sectionToggle.addEventListener("click", () => {
      const collapsed = !evidenceList.classList.contains("hidden");
      setEvidenceVisibility(collapsed);
    });
    setEvidenceVisibility(state.evidenceCollapsed);

    recommendationGrid.append(card);
  });
}

function getFilteredItems() {
  const filter = topicFilterInput.value.trim().toLowerCase();
  let items = [...state.recommendations];
  if (filter) {
    items = items.filter((item) => {
      const haystack = [
        item.topic,
        item.recommendation,
        item.suggested_angle,
        item.why_now,
      ]
        .join(" ")
        .toLowerCase();
      return haystack.includes(filter);
    });
  }

  if (sortModeSelect.value === "topic") {
    items.sort((left, right) => left.topic.localeCompare(right.topic));
  } else {
    items.sort((left, right) => new Date(right.generated_at) - new Date(left.generated_at));
  }
  return items;
}

function rerender() {
  const items = getFilteredItems();
  renderSummary(items);
  renderRecommendations(items);
}

async function loadRecommendations() {
  state.audienceId = audienceInput.value.trim();
  if (!state.audienceId) {
    setStatus("Missing audience");
    state.recommendations = [];
    rerender();
    return;
  }

  setStatus("Loading");
  try {
    const response = await fetch(`/recommendations?audience_id=${encodeURIComponent(state.audienceId)}`);
    if (!response.ok) {
      throw new Error(`Request failed with ${response.status}`);
    }
    state.recommendations = await response.json();
    setStatus("Ready");
    rerender();
  } catch (error) {
    console.error(error);
    setStatus("Error");
    state.recommendations = [];
    rerender();
  }
}

refreshButton.addEventListener("click", loadRecommendations);
topicFilterInput.addEventListener("input", rerender);
sortModeSelect.addEventListener("change", rerender);
toggleEvidenceButton.addEventListener("click", () => {
  state.evidenceCollapsed = !state.evidenceCollapsed;
  toggleEvidenceButton.textContent = state.evidenceCollapsed ? "Expand Evidence" : "Collapse Evidence";
  rerender();
});

loadRecommendations();
