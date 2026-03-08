import { startTransition, useDeferredValue, useEffect, useMemo, useState } from "react";

const DEFAULT_AUDIENCE_ID = "b3564638-516e-4592-98b6-a21cc59a27cb";
const ALL_TOPICS = "All";

function formatDate(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(date);
}

function compareByNewest(left, right) {
  return new Date(right.generated_at) - new Date(left.generated_at);
}

function compareByTopic(left, right) {
  return left.topic.localeCompare(right.topic);
}

function getWhyNowSnippet(value) {
  const [firstSentence] = value.split(". ");
  return firstSentence ? `${firstSentence}.` : value;
}

function buildTopicOptions(recommendations) {
  const counts = new Map();
  recommendations.forEach((item) => {
    counts.set(item.topic, (counts.get(item.topic) ?? 0) + 1);
  });

  return [
    { label: ALL_TOPICS, count: recommendations.length },
    ...Array.from(counts.entries())
      .sort((left, right) => right[1] - left[1] || left[0].localeCompare(right[0]))
      .map(([label, count]) => ({ label, count })),
  ];
}

function buildQuickStats(recommendations) {
  const evidenceCount = recommendations.reduce((sum, item) => sum + item.evidence.length, 0);
  const hookCount = recommendations.reduce((sum, item) => sum + item.draft_hooks.length, 0);

  return [
    { label: "Live ideas", value: recommendations.length },
    { label: "Evidence lines", value: evidenceCount },
    { label: "Angles", value: hookCount },
  ];
}

function statusClasses(status) {
  if (status === "Ready") {
    return "bg-mint-450/12 text-mint-450";
  }
  if (status === "Error") {
    return "bg-coral-450/12 text-coral-450";
  }
  return "bg-gold-350/18 text-amber-700";
}

export default function App() {
  const [audienceId] = useState(DEFAULT_AUDIENCE_ID);
  const [search, setSearch] = useState("");
  const [sortMode, setSortMode] = useState("recent");
  const [status, setStatus] = useState("Loading");
  const [recommendations, setRecommendations] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [collapsedEvidence, setCollapsedEvidence] = useState(false);
  const [activeTopic, setActiveTopic] = useState(ALL_TOPICS);

  const deferredSearch = useDeferredValue(search);

  useEffect(() => {
    let ignore = false;

    async function loadRecommendations() {
      setStatus("Loading");
      try {
        const response = await fetch(`/recommendations?audience_id=${encodeURIComponent(audienceId)}`);
        if (!response.ok) {
          throw new Error(`Request failed with ${response.status}`);
        }
        const payload = await response.json();
        if (ignore) {
          return;
        }
        setRecommendations(payload);
        setSelectedId((current) => current ?? payload[0]?.id ?? null);
        setStatus("Ready");
      } catch (error) {
        console.error(error);
        if (!ignore) {
          setRecommendations([]);
          setSelectedId(null);
          setStatus("Error");
        }
      }
    }

    loadRecommendations();
    return () => {
      ignore = true;
    };
  }, [audienceId]);

  const topicOptions = useMemo(() => buildTopicOptions(recommendations), [recommendations]);

  const visibleRecommendations = useMemo(() => {
    const normalizedQuery = deferredSearch.trim().toLowerCase();
    let items = recommendations.filter((item) => {
      if (activeTopic !== ALL_TOPICS && item.topic !== activeTopic) {
        return false;
      }

      if (!normalizedQuery) {
        return true;
      }

      return [
        item.topic,
        item.recommendation,
        item.suggested_angle,
        item.why_now,
        item.audience_fit,
        ...item.evidence.map((entry) => entry.evidence_text),
      ]
        .join(" ")
        .toLowerCase()
        .includes(normalizedQuery);
    });

    items = [...items].sort(sortMode === "topic" ? compareByTopic : compareByNewest);
    return items;
  }, [activeTopic, deferredSearch, recommendations, sortMode]);

  useEffect(() => {
    if (activeTopic !== ALL_TOPICS && !topicOptions.some((option) => option.label === activeTopic)) {
      setActiveTopic(ALL_TOPICS);
    }
  }, [activeTopic, topicOptions]);

  useEffect(() => {
    if (!visibleRecommendations.some((item) => item.id === selectedId)) {
      setSelectedId(visibleRecommendations[0]?.id ?? null);
    }
  }, [selectedId, visibleRecommendations]);

  const selectedRecommendation =
    visibleRecommendations.find((item) => item.id === selectedId) ?? visibleRecommendations[0] ?? null;

  const quickStats = useMemo(() => buildQuickStats(visibleRecommendations), [visibleRecommendations]);

  function handleRefresh() {
    setSelectedId(null);
    setStatus("Loading");
    fetch(`/recommendations?audience_id=${encodeURIComponent(audienceId)}`)
      .then((response) => {
        if (!response.ok) {
          throw new Error(`Request failed with ${response.status}`);
        }
        return response.json();
      })
      .then((payload) => {
        setRecommendations(payload);
        setSelectedId(payload[0]?.id ?? null);
        setStatus("Ready");
      })
      .catch((error) => {
        console.error(error);
        setRecommendations([]);
        setStatus("Error");
      });
  }

  function handleFilterChange(event) {
    const value = event.target.value;
    startTransition(() => {
      setSearch(value);
    });
  }

  return (
    <div className="mx-auto grid min-h-screen w-[min(1480px,calc(100vw-28px))] grid-cols-1 gap-5 py-5 xl:grid-cols-[300px_minmax(0,1fr)]">
      <aside className="flex flex-col gap-4">
        <div className="glass-panel rounded-[32px] bg-[linear-gradient(145deg,rgba(22,159,150,0.14),rgba(233,132,88,0.08))] p-5">
          <div className="flex items-start gap-4">
            <div className="grid h-[72px] w-[72px] shrink-0 place-items-center rounded-[24px] bg-linear-to-br from-aqua-450 to-coral-450 text-lg font-extrabold tracking-[0.08em] text-white shadow-[0_16px_28px_rgba(22,159,150,0.22)]">
              XR
            </div>
            <div className="min-w-0">
              <p className="eyebrow">Crypto narrative radar</p>
              <h1 className="mt-1 font-display text-[1.75rem] leading-[0.95] tracking-[-0.05em] text-ink-950">
                Evidence-first idea feed
              </h1>
              <p className="mt-3 text-sm leading-6 text-ink-600">
                Public-profile fixture data, tightened into explainer recommendations you can actually post from.
              </p>
            </div>
          </div>
          <div className="mt-4 grid grid-cols-3 gap-3">
            {quickStats.map((stat) => (
              <div
                key={stat.label}
                className="rounded-[22px] border border-white/70 bg-white/80 px-4 py-3 shadow-[0_14px_30px_rgba(31,23,44,0.08)]"
              >
                <p className="text-[0.68rem] font-semibold uppercase tracking-[0.18em] text-ink-600">{stat.label}</p>
                <strong className="mt-1 block text-2xl font-semibold tracking-[-0.06em] text-ink-950">
                  {stat.value}
                </strong>
              </div>
            ))}
          </div>
        </div>

        <section className="glass-panel p-5">
          <div className="flex items-center justify-between gap-3">
            <span className="eyebrow">Topic lens</span>
            <button
              className="rounded-full bg-black/5 px-4 py-2 text-sm font-medium text-ink-950 transition hover:bg-black/8"
              type="button"
              onClick={handleRefresh}
            >
              Refresh
            </button>
          </div>
          <div className="mt-4 grid gap-3">
            {topicOptions.map((topic) => (
              <button
                key={topic.label}
                type="button"
                className={[
                  "flex items-center justify-between gap-3 rounded-[20px] border px-4 py-3 text-left transition hover:-translate-y-0.5",
                  activeTopic === topic.label
                    ? "border-aqua-350/40 bg-linear-to-br from-aqua-450/12 to-white shadow-[0_14px_28px_rgba(22,159,150,0.12)]"
                    : "border-black/6 bg-white/88",
                ].join(" ")}
                onClick={() => setActiveTopic(topic.label)}
              >
                <span className="font-semibold text-ink-950">{topic.label}</span>
                <small className="text-sm text-ink-600">
                  {topic.count} item{topic.count === 1 ? "" : "s"}
                </small>
              </button>
            ))}
          </div>
        </section>

        <section className="glass-panel-soft p-5">
          <div className="flex items-center justify-between gap-3">
            <span className="eyebrow">Snapshot</span>
            <span className={`rounded-full px-3 py-1.5 text-xs font-semibold ${statusClasses(status)}`}>
              {status}
            </span>
          </div>
          <div className="mt-4 space-y-3">
            <div className="rounded-[22px] border border-black/6 bg-white/84 px-4 py-4">
              <p className="text-sm font-medium text-ink-600">Audience</p>
              <p className="mt-1 text-sm leading-6 text-ink-800">
                Crypto builders, researchers, and market infrastructure operators.
              </p>
            </div>
            <div className="rounded-[22px] border border-black/6 bg-white/84 px-4 py-4">
              <p className="text-sm font-medium text-ink-600">Current filter</p>
              <p className="mt-1 text-sm leading-6 text-ink-800">
                {activeTopic === ALL_TOPICS ? "All topics" : activeTopic}
              </p>
            </div>
          </div>
        </section>
      </aside>

      <main className="flex min-w-0 flex-col gap-4">
        <header className="glass-panel flex flex-col gap-5 rounded-[32px] p-6 lg:flex-row lg:items-end lg:justify-between">
          <div className="min-w-0">
            <p className="eyebrow">Recommendation workspace</p>
            <h2 className="mt-1 max-w-[11ch] font-display text-[clamp(2rem,4vw,3.5rem)] leading-[0.9] tracking-[-0.06em] text-ink-950">
              Scan, pick, then write
            </h2>
          </div>

          <div className="flex flex-1 flex-col gap-3 sm:flex-row sm:flex-wrap sm:justify-end">
            <label className="flex min-w-0 flex-1 flex-col gap-2 sm:min-w-[240px]">
              <span className="eyebrow">Filter feed</span>
              <input
                className="field-shell"
                placeholder="Search topic, angle, evidence…"
                value={search}
                onChange={handleFilterChange}
              />
            </label>

            <label className="flex min-w-0 flex-col gap-2 sm:min-w-[200px]">
              <span className="eyebrow">Order</span>
              <select className="field-shell" value={sortMode} onChange={(event) => setSortMode(event.target.value)}>
                <option value="recent">Newest first</option>
                <option value="topic">Topic A-Z</option>
              </select>
            </label>
          </div>
        </header>

        <section className="story-scroll grid auto-cols-[minmax(180px,1fr)] grid-flow-col gap-3 overflow-x-auto pb-2 max-md:auto-cols-[minmax(160px,72%)]">
          {visibleRecommendations.map((item) => (
            <button
              key={item.id}
              type="button"
              className={[
                "glass-panel-soft min-h-[92px] px-4 py-4 text-left transition hover:-translate-y-0.5",
                selectedRecommendation?.id === item.id
                  ? "border border-aqua-350/40 bg-linear-to-br from-aqua-450/12 to-white shadow-[0_18px_34px_rgba(22,159,150,0.12)]"
                  : "",
              ].join(" ")}
              onClick={() => setSelectedId(item.id)}
            >
              <span className="block font-semibold text-ink-950">{item.topic}</span>
              <small className="mt-1 block text-sm text-ink-600">{item.evidence.length} proof points</small>
            </button>
          ))}
        </section>

        <section className="grid gap-4 xl:grid-cols-[minmax(0,1.08fr)_minmax(330px,0.92fr)]">
          <div className="grid gap-4">
            {visibleRecommendations.map((item, index) => (
              <article
                key={item.id}
                className={[
                  "glass-panel cursor-pointer p-5 transition hover:-translate-y-0.5",
                  selectedRecommendation?.id === item.id
                    ? "border-aqua-350/40 shadow-[0_24px_48px_rgba(22,159,150,0.12)]"
                    : "",
                ].join(" ")}
                onClick={() => setSelectedId(item.id)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    setSelectedId(item.id);
                  }
                }}
                role="button"
                tabIndex={0}
              >
                <div className="flex items-center justify-between gap-3 text-sm text-ink-600">
                  <span className="eyebrow">#{String(index + 1).padStart(2, "0")}</span>
                  <time>{formatDate(item.generated_at)}</time>
                </div>

                <div className="mt-4 flex items-center gap-3">
                  <span className="h-3 w-3 rounded-full bg-linear-to-br from-aqua-450 to-coral-450" />
                  <p className="m-0 font-semibold text-ink-950">{item.topic}</p>
                </div>

                <h3 className="mt-4 font-display text-[1.45rem] leading-[1.18] tracking-[-0.05em] text-ink-950">
                  {item.recommendation}
                </h3>
                <p className="mt-3 text-sm leading-6 text-ink-600">{getWhyNowSnippet(item.why_now)}</p>

                <div className="mt-4 grid gap-2">
                  {item.evidence.slice(0, 2).map((entry) => (
                    <p
                      key={`${item.id}-${entry.evidence_text}`}
                      className="m-0 rounded-2xl bg-black/4 px-3 py-2.5 text-sm leading-5 text-ink-800"
                    >
                      {entry.evidence_text}
                    </p>
                  ))}
                </div>

                <div className="mt-4 flex items-center justify-between gap-3 text-sm text-ink-600">
                  <span className="capitalize">{item.format.replace("_", " ")}</span>
                  <span>{item.draft_hooks.length} ways in</span>
                </div>
              </article>
            ))}
          </div>

          <aside className="glass-panel sticky top-4 self-start p-6 max-xl:static">
            {selectedRecommendation ? (
              <div className="flex flex-col gap-4">
                <div>
                  <p className="eyebrow text-aqua-450">{selectedRecommendation.topic}</p>
                  <h2 className="mt-2 font-display text-[1.95rem] leading-[1.05] tracking-[-0.06em] text-ink-950">
                    {selectedRecommendation.recommendation}
                  </h2>
                  <p className="mt-3 text-sm leading-6 text-ink-600">{selectedRecommendation.audience_fit}</p>
                </div>

                <section className="rounded-[24px] border border-white/70 bg-linear-to-br from-aqua-450/10 to-sand-300/35 p-5">
                  <span className="eyebrow">Angle to write</span>
                  <p className="mt-3 text-sm leading-6 text-ink-800">{selectedRecommendation.suggested_angle}</p>
                </section>

                <section className="rounded-[24px] border border-black/6 bg-white/86 p-5">
                  <span className="eyebrow">Snapshot read</span>
                  <p className="mt-3 text-sm leading-6 text-ink-800">{selectedRecommendation.why_now}</p>
                </section>

                <section className="rounded-[24px] border border-black/6 bg-white/86 p-5">
                  <div className="flex items-center justify-between gap-3">
                    <span className="eyebrow">Evidence</span>
                    <button
                      className="rounded-full bg-black/5 px-3 py-1.5 text-xs font-medium text-ink-950 transition hover:bg-black/8"
                      type="button"
                      onClick={() => setCollapsedEvidence((current) => !current)}
                    >
                      {collapsedEvidence ? "Show" : "Hide"}
                    </button>
                  </div>
                  {!collapsedEvidence ? (
                    <ul className="mt-3 grid gap-3 pl-5 text-sm leading-6 text-ink-800">
                      {selectedRecommendation.evidence.map((item) => (
                        <li key={`${selectedRecommendation.id}-${item.evidence_text}`}>{item.evidence_text}</li>
                      ))}
                    </ul>
                  ) : (
                    <p className="mt-3 text-sm leading-6 text-ink-600">Evidence hidden for quicker scanning.</p>
                  )}
                </section>

                <section className="rounded-[24px] border border-black/6 bg-white/86 p-5">
                  <span className="eyebrow">Ways in</span>
                  <ul className="mt-3 grid gap-3 pl-5 text-sm leading-6 text-ink-800">
                    {selectedRecommendation.draft_hooks.map((hook) => (
                      <li key={`${selectedRecommendation.id}-${hook}`}>{hook}</li>
                    ))}
                  </ul>
                </section>

                <section className="rounded-[24px] border border-black/6 bg-white/86 p-5">
                  <span className="eyebrow">Cautions</span>
                  <ul className="mt-3 grid gap-3 pl-5 text-sm leading-6 text-orange-800">
                    {selectedRecommendation.risks.map((risk) => (
                      <li key={`${selectedRecommendation.id}-${risk}`}>{risk}</li>
                    ))}
                  </ul>
                </section>
              </div>
            ) : (
              <div className="py-2">
                <h2 className="font-display text-3xl tracking-[-0.05em] text-ink-950">No recommendations found</h2>
                <p className="mt-3 text-sm leading-6 text-ink-600">
                  Refresh the feed or rerun the fixture pipeline to repopulate the snapshot.
                </p>
              </div>
            )}
          </aside>
        </section>
      </main>
    </div>
  );
}
