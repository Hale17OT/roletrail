<script setup>
import {
  ref,
  reactive,
  computed,
  watch,
  onMounted,
  onUnmounted,
  nextTick,
} from "vue";
import {
  stages,
  stageLabel,
  emptyFilters,
  eligible,
  selectJobs,
  readCache,
  routeFrom,
  routeTo,
  safeLink,
  displayDate,
} from "./domain/jobs.mjs";
import RoletrailSelect from "./components/RoletrailSelect.vue";
import {
  Compass,
  Send,
  Radar,
  Files,
  UserRound,
  ArrowUpRight,
  RotateCw,
  Search,
  X,
  Download,
  Mail,
  GraduationCap,
  ShieldCheck,
} from "@lucide/vue";
import JobRoletrail from "./components/JobRoletrail.vue";
import SourceWorkspace from "./components/SourceWorkspace.vue";
import OpportunityAudit from "./components/OpportunityAudit.vue";
let preferences = {};
try {
  const p = JSON.parse(localStorage.getItem("dossier.view.v2"));
  if (p && typeof p === "object") preferences = p;
} catch {}
const initial = routeFrom(
    location.search ||
      routeTo(
        preferences.view || "opportunities",
        preferences.views?.[preferences.view] || emptyFilters(),
      ),
  ),
  view = ref(initial.view),
  filters = reactive(initial.filters),
  selectedId = ref(initial.job);
const jobs = ref([]),
  cvs = ref([]),
  profile = ref(null),
  integrations = ref(null),
  searches = ref([]);
const loading = ref(true),
  offline = ref(false),
  error = ref(""),
  notice = ref(""),
  busy = ref(""),
  blacklistQuery = ref(""),
  limit = ref(30),
  appLayout = ref("list"),
  showFilters = ref(false);
const navigationIcons = {
  opportunities: Compass,
  applications: Send,
  sources: Radar,
  cvs: Files,
  settings: UserRound,
};
const filterOptions = {
  arrangement: [
    { value: "", label: "All arrangements" },
    { value: "remote", label: "Remote" },
    { value: "local", label: "Local Addis Ababa" },
    { value: "sponsored", label: "Visa sponsored" },
    { value: "unknown", label: "Not verified" },
  ],
  focus: [
    { value: "", label: "All skills" },
    { value: "power", label: "Power Platform" },
    { value: "dynamics", label: "Dynamics 365" },
    { value: "automation", label: "Automation" },
    { value: ".net", label: ".NET" },
  ],
  region: [
    { value: "", label: "All regions" },
    { value: "ethiopia", label: "Local Ethiopia" },
    { value: "international", label: "Worldwide / EMEA / Africa" },
    { value: "uk", label: "United Kingdom" },
    { value: "us", label: "United States" },
    { value: "other", label: "Other / unverified" },
  ],
  sort: [
    { value: "recent", label: "Verified publication" },
    { value: "company", label: "Employer A–Z" },
    { value: "match", label: "Internal fit score" },
    { value: "action", label: "Next action date" },
  ],
};
const stageOptions = computed(() => [
  { value: "", label: "All stages" },
  ...(view.value === "applications" ? stages : ["ready", "saved"]).map(
    (value) => ({
      value,
      label: stageLabel(value),
    }),
  ),
]);
const navigation = [
  ["opportunities", "Opportunities"],
  ["applications", "Applications"],
  ["sources", "Search & Sources"],
  ["cvs", "CVs"],
  ["settings", "Profile & Settings"],
];
const selected = computed(() =>
  jobs.value.find((j) => j.id === selectedId.value),
);
const visible = computed(() => selectJobs(jobs.value, view.value, filters));
const pageJobs = computed(() =>
  view.value === "applications" && appLayout.value === "stages"
    ? visible.value
    : visible.value.slice(0, limit.value),
);
const opportunityJobs = computed(() =>
  jobs.value.filter((j) => ["ready", "saved"].includes(j.status)),
);
const verifiedCount = computed(
  () => opportunityJobs.value.filter(eligible).length,
);
const protectedCount = computed(
  () => jobs.value.filter((j) => stages.includes(j.status)).length,
);
const blacklist = computed(() =>
  jobs.value.filter(
    (j) =>
      j.status === "deleted" &&
      `${j.company} ${j.title} ${j.deletionReason || ""}`
        .toLowerCase()
        .includes(blacklistQuery.value.toLowerCase()),
  ),
);
const activeRun = computed(() =>
  searches.value.find((r) => ["running", "cancelling"].includes(r.state)),
);
let timer, returnFocus;
async function api(path, data) {
  const r = await fetch(path, {
    cache: "no-store",
    ...(data === undefined
      ? {}
      : {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(data),
        }),
  });
  const result = await r
    .json()
    .catch(() => ({ error: "Unreadable server response" }));
  if (!r.ok)
    throw new Error(
      result.error ||
        (result.status === "failed"
          ? "All email accounts failed. Review account results."
          : `Request failed (${r.status})`),
    );
  return result;
}
function cache(data) {
  try {
    localStorage.setItem(
      "dossier.jobs.v2",
      JSON.stringify({
        version: 2,
        jobs: data,
        savedAt: new Date().toISOString(),
      }),
    );
  } catch {}
}
async function loadJobs() {
  const data = await api("/api/jobs");
  if (!Array.isArray(data)) throw new Error("Invalid job response");
  jobs.value = data;
  offline.value = false;
  cache(data);
}
async function reload() {
  loading.value = true;
  error.value = "";
  try {
    await loadJobs();
    notice.value = "";
  } catch (e) {
    offline.value = true;
    error.value = e.message;
    let cached;
    try {
      cached = readCache(localStorage.getItem("dossier.jobs.v2"));
    } catch {}
    if (cached) {
      jobs.value = cached.jobs;
      notice.value = `Offline snapshot from ${displayDate(cached.savedAt)}. Reconnect to make changes.`;
    } else
      notice.value = "No usable cached data. Start the local server and retry.";
  } finally {
    loading.value = false;
  }
}
async function ancillary() {
  const r = await Promise.allSettled([
    api("/api/cvs"),
    api("/api/profile"),
    api("/api/integrations"),
    api("/api/searches"),
    api("/api/health"),
  ]);
  [cvs, profile, integrations, searches].forEach((target, i) => {
    if (r[i].status === "fulfilled") target.value = r[i].value;
  });
  if (r[4].status === "fulfilled" && r[4].value.frontendStale)
    notice.value =
      "The dashboard source changed after this build. Run npm run build in frontend, then reload.";
  if (r.some((x) => x.status === "rejected"))
    notice.value =
      "Some supporting data could not be loaded. Refresh to retry.";
}
async function refreshSupporting() {
  const results = await Promise.allSettled([ancillary(), loadJobs()]);
  if (results[1].status === "rejected") {
    offline.value = true;
    error.value =
      "The latest job data could not be refreshed. Reconnect to see the saved integration results.";
  }
}
function navigate(next) {
  preferences.views ??= {};
  preferences.views[view.value] = { ...filters };
  view.value = next;
  Object.assign(
    filters,
    routeFrom(routeTo(next, preferences.views[next] || emptyFilters())).filters,
  );
  selectedId.value = "";
  limit.value = 30;
  history.pushState({}, "", routeTo(next, filters));
}
function openJob(j) {
  if (!j) return;
  returnFocus = document.activeElement;
  selectedId.value = j.id;
}
function closeJob() {
  selectedId.value = "";
  nextTick(() => {
    if (returnFocus?.isConnected) returnFocus.focus();
    else document.querySelector("#main h1")?.focus();
  });
}
function replaceJob(j) {
  const i = jobs.value.findIndex((x) => x.id === j.id);
  if (i >= 0) jobs.value.splice(i, 1, j);
  else jobs.value.push(j);
  cache(jobs.value);
}
async function mutate(payload) {
  if (offline.value || busy.value || !selected.value) return false;
  const j = selected.value;
  const focusBefore = document.activeElement;
  busy.value = j.id;
  error.value = "";
  notice.value = "";
  try {
    const fresh = await api("/api/jobs/action", {
      id: j.id,
      revision: j._meta.revision,
      ...payload,
    });
    replaceJob(fresh);
    notice.value = "Changes saved.";
    if (fresh.status === "deleted") closeJob();
    return true;
  } catch (e) {
    error.value = e.message;
    return false;
  } finally {
    busy.value = "";
    await nextTick();
    const modal = [...document.querySelectorAll("dialog[open]")].at(-1);
    if (
      focusBefore?.isConnected &&
      !focusBefore.disabled &&
      (!modal || modal.contains(focusBefore))
    )
      focusBefore.focus();
    else modal?.querySelector("button:not([disabled])")?.focus();
  }
}
async function recheck() {
  if (offline.value || busy.value || !selected.value) return;
  busy.value = selected.value.id;
  error.value = "";
  notice.value = "Checking the live official source…";
  try {
    const j = await api("/api/jobs/recheck", { id: selected.value.id });
    replaceJob(j);
    notice.value = j._meta.eligibility.label;
  } catch (e) {
    error.value = e.message;
  } finally {
    busy.value = "";
  }
}
async function poll() {
  try {
    const running = !!activeRun.value;
    searches.value = await api("/api/searches");
    if (running && !activeRun.value) {
      await loadJobs();
      integrations.value = await api("/api/integrations");
      notice.value = "Search ended. Review coverage and results.";
    }
  } catch {
    offline.value = true;
    if (activeRun.value)
      error.value =
        "Search progress disconnected. Reconnect to recover saved status.";
  }
}
function restoreRoute() {
  const r = routeFrom(location.search);
  view.value = r.view;
  Object.assign(filters, r.filters);
  selectedId.value = r.job;
}
watch(
  [view, filters, selectedId],
  () => {
    history.replaceState(
      {},
      "",
      routeTo(view.value, filters, selectedId.value),
    );
    preferences.view = view.value;
    preferences.views ??= {};
    preferences.views[view.value] = { ...filters };
    try {
      localStorage.setItem("dossier.view.v2", JSON.stringify(preferences));
    } catch {}
  },
  { deep: true },
);
watch(
  [view, filters],
  () => {
    limit.value = 30;
  },
  { deep: true },
);
onMounted(async () => {
  await Promise.all([reload(), ancillary()]);
  timer = setInterval(poll, 5000);
  window.addEventListener("popstate", restoreRoute);
});
onUnmounted(() => {
  clearInterval(timer);
  window.removeEventListener("popstate", restoreRoute);
});
</script>

<template>
  <div class="workspace">
    <aside class="sidebar">
      <a
        class="brand"
        href="?view=opportunities"
        @click.prevent="navigate('opportunities')"
        ><span class="brand-mark">R.</span
        ><span>Roletrail<small>CAREER WORKSPACE</small></span></a
      >
      <nav aria-label="Workspace">
        <button
          v-for="[id, label] in navigation"
          :key="id"
          :data-view="id"
          :class="{ active: view === id }"
          :aria-current="view === id ? 'page' : undefined"
          @click="navigate(id)"
        >
          <component
            :is="navigationIcons[id]"
            :size="18"
            aria-hidden="true"
          /><span>{{ label }}</span
          ><span class="nav-count" v-if="id === 'opportunities'">{{
            verifiedCount
          }}</span
          ><span class="nav-count" v-if="id === 'applications'">{{
            protectedCount
          }}</span>
        </button>
      </nav>
      <div class="sidebar-foot">
        <span class="connection-dot" :class="{ disconnected: offline }"></span
        >{{ offline ? "Offline snapshot" : "Local workspace"
        }}<small>Local-first · Private data</small>
      </div>
    </aside>
    <main id="main" :key="view" class="view-surface">
      <header class="page-header">
        <div>
          <h1 tabindex="-1">
            {{ navigation.find((n) => n[0] === view)?.[1] }}
          </h1>
          <p class="page-subtitle">
            {{
              view === "opportunities"
                ? "Find a role worth your next application."
                : view === "applications"
                  ? "Follow your applications and decide the next step."
                  : view === "sources"
                    ? "Search coverage, connections, and evidence."
                    : view === "cvs"
                      ? "Your materials, pairings, and file versions."
                      : "Your candidate facts and preserved blacklist."
            }}
          </p>
        </div>
        <button
          class="quiet"
          :disabled="loading"
          @click="
            reload();
            ancillary();
          "
        >
          <RotateCw
            :size="16"
            :class="{ 'is-spinning': loading }"
            aria-hidden="true"
          />{{ loading ? "Loading…" : "Refresh data" }}
        </button>
      </header>
      <div class="feedback" aria-live="polite" role="status" v-if="notice">
        {{ notice
        }}<button aria-label="Dismiss notice" @click="notice = ''">
          <X :size="16" aria-hidden="true" />
        </button>
      </div>
      <div class="feedback error" role="alert" v-if="error">
        {{ error
        }}<button aria-label="Dismiss error" @click="error = ''">
          <X :size="16" aria-hidden="true" />
        </button>
      </div>
      <div class="feedback warning" v-if="offline">
        Offline: changes, verification, and searches are disabled.
        <button @click="reload">Reconnect</button>
      </div>
      <div class="loading-state" v-if="loading && !jobs.length">
        Loading your workspace…
      </div>
      <template v-else-if="view === 'opportunities' || view === 'applications'">
        <OpportunityAudit v-if="view === 'opportunities'" :report="integrations?.opportunityReview" />
        <div class="section-heading">
          <div
            class="segments"
            v-if="view === 'opportunities'"
            aria-label="Eligibility"
          >
            <button
              :class="{ selected: filters.eligibility === 'verified' }"
              @click="filters.eligibility = 'verified'"
            >
              Verified <span>{{ verifiedCount }}</span></button
            ><button
              :class="{ selected: filters.eligibility === 'review' }"
              @click="filters.eligibility = 'review'"
            >
              Needs review
              <span>{{ opportunityJobs.length - verifiedCount }}</span></button
            ><button
              :class="{ selected: filters.eligibility === 'all' }"
              @click="filters.eligibility = 'all'"
            >
              All
            </button>
          </div>
          <div class="segments" v-else aria-label="Applications layout">
            <button
              :class="{ selected: appLayout === 'list' }"
              @click="appLayout = 'list'"
            >
              List</button
            ><button
              :class="{ selected: appLayout === 'stages' }"
              @click="appLayout = 'stages'"
            >
              By stage
            </button>
          </div>
          <p class="count">
            <strong>{{ visible.length }}</strong>
            {{
              visible.length > pageJobs.length
                ? "matching roles · " + pageJobs.length + " loaded"
                : "roles shown"
            }}
          </p>
        </div>
        <div class="filters" :class="{ expanded: showFilters }">
          <label class="search-field"
            >Search roles<input
              v-model="filters.q"
              type="search"
              placeholder="Title, employer, or skill" /></label
          ><button
            class="filter-toggle"
            :aria-expanded="showFilters"
            @click="showFilters = !showFilters"
          >
            {{ showFilters ? "Hide advanced filters" : "Refine filters"
            }}<span
              >{{
                [
                  filters.stage,
                  filters.arrangement,
                  filters.focus,
                  filters.region,
                ].filter(Boolean).length
              }}
              active</span
            ></button
          ><RoletrailSelect
            v-model="filters.stage"
            label="Stage"
            :options="stageOptions"
          /><RoletrailSelect
            v-model="filters.arrangement"
            label="Arrangement"
            :options="filterOptions.arrangement"
          /><RoletrailSelect
            v-model="filters.focus"
            label="Focus"
            :options="filterOptions.focus"
          /><RoletrailSelect
            v-model="filters.region"
            label="Region"
            :options="filterOptions.region"
          /><div class="sort-with-clear"><RoletrailSelect
            v-model="filters.sort"
            label="Sort"
            :options="filterOptions.sort"
          /><button
            class="quiet clear-filters"
            @click="
              Object.assign(filters, emptyFilters());
              filters.eligibility = 'all';
            "
          >
            Clear filters
          </button>
          </div>
        </div>
        <p
          class="context-note"
          v-if="view === 'opportunities' && filters.eligibility !== 'verified'"
        >
          Legacy records retain their original stage. Refresh the official
          source before applying. A fit score is an internal assessment, not a
          hiring probability.
        </p>
        <div class="empty-state" v-if="!visible.length">
          <Search class="empty-glyph" :size="28" aria-hidden="true" />
          <h2>
            {{
              view === "opportunities" && filters.eligibility === "verified"
                ? "No currently verified opportunities"
                : "No roles match these filters"
            }}
          </h2>
          <p>
            {{
              view === "opportunities"
                ? "A role needs a complete kit and a source check within 24 hours. Prefer postings within 30 days; older or undated roles need a freshly verified open application."
                : "Try another stage or clear your search."
            }}
          </p>
          <div class="button-row">
            <button
              v-if="view === 'opportunities'"
              @click="filters.eligibility = 'review'"
            >
              Review existing leads</button
            ><button class="primary" @click="navigate('sources')">
              Search for matching jobs
            </button>
          </div>
        </div>
        <template v-else
          ><div
            v-for="group in view === 'applications' && appLayout === 'stages'
              ? stages.filter((s) => visible.some((j) => j.status === s))
              : ['all']"
            :key="group"
            class="job-group"
          >
            <h2 v-if="group !== 'all'" class="group-heading">
              {{ stageLabel(group) }}
              <span>{{
                visible.filter((j) => j.status === group).length
              }}</span>
            </h2>
            <div class="job-list" role="list">
              <article
                class="job-row"
                v-for="job in pageJobs.filter(
                  (j) => group === 'all' || j.status === group,
                )"
                :key="job.id"
                role="listitem"
              >
                <div class="employer-monogram" aria-hidden="true">
                  {{ job.company.slice(0, 1) }}
                </div>
                <div class="job-main">
                  <p class="company">{{ job.company }}</p>
                  <h2>
                    <button class="title-link" @click="openJob(job)">
                      {{ job.title }}
                    </button>
                  </h2>
                  <p class="job-region">
                    {{ job._meta?.geography?.destination || job.region }}
                  </p>
                  <div class="tags">
                    <span
                      v-for="tag in (job.tags || []).slice(0, 4)"
                      :key="tag"
                      :data-skill="tag"
                      >{{ tag }}</span
                    >
                  </div>
                  <p class="next-action" v-if="job.nextAction?.text">
                    Next: {{ job.nextAction.text }}
                    <span class="mono" v-if="job.nextAction.due"
                      >· {{ job.nextAction.due }}</span
                    >
                  </p>
                </div>
                <div class="job-facts">
                  <span class="stage-badge" :data-stage="job.status">{{
                    stageLabel(job.status)
                  }}</span>
                  <p class="salary mono">
                    {{ job.salary || "Not quoted in posting" }}
                  </p>
                  <p class="verification" :class="{ verified: eligible(job) }">
                    {{ job._meta?.eligibility?.label || "Needs verification" }}
                  </p>
                  <p class="date mono">
                    {{
                      job._meta?.eligibility?.publishedAt
                        ? displayDate(job._meta.eligibility.publishedAt)
                        : "Publication date not verified"
                    }}
                  </p>
                </div>
                <button class="review-button" @click="openJob(job)">
                  Review <ArrowUpRight :size="16" aria-hidden="true" />
                </button>
              </article>
            </div>
          </div>
          <button
            class="load-more"
            v-if="visible.length > pageJobs.length"
            @click="limit += 30"
          >
            Show 30 more · {{ visible.length - limit }} remaining
          </button></template
        >
      </template>
      <SourceWorkspace
        v-else-if="view === 'sources'"
        :integrations="integrations"
        :searches="searches"
        :offline="offline"
        :api="api"
        @refresh="refreshSupporting"
        @error="error = $event"
        @notice="notice = $event"
        @open="openJob(jobs.find((j) => j.id === $event))"
      />
      <template v-else-if="view === 'cvs'">
        <p class="context-note">
          Versions are SHA-256 fingerprints. Pairings reflect current records.
          No unsupported ATS acceptance score is displayed.
        </p>
        <section class="panel cv-panel" v-for="cv in cvs" :key="cv.fileName">
          <div class="panel-heading">
            <div>
              <p class="cv-version mono">
                Version <span class="mono">{{ cv.version }}</span>
              </p>
              <h2 class="section-title">
                <Files :size="20" aria-hidden="true" />{{ cv.roleTitle }}
              </h2>
            </div>
            <div class="cv-actions" v-if="cv.available">
              <a
                v-if="cv.available"
                class="button"
                :href="'/cvs/' + encodeURIComponent(cv.fileName)"
                target="_blank"
                rel="noopener noreferrer"
                >Open PDF <ArrowUpRight :size="16" aria-hidden="true" /></a
              ><a
                class="button download-button"
                :href="'/cvs/' + encodeURIComponent(cv.fileName)"
                :download="cv.fileName"
                :aria-label="'Download ' + cv.shortLabel + ' CV'"
                title="Download CV"
                ><Download :size="18" aria-hidden="true"
              /></a>
            </div>
            <span v-else class="error-text">PDF unavailable</span>
          </div>
          <p>{{ cv.summary }}</p>
          <div class="tags">
            <span v-for="s in cv.skills" :key="s" :data-skill="s">{{ s }}</span>
          </div>
          <p class="review-notice">{{ cv.reviewNotice }}</p>
          <details>
            <summary>{{ cv.pairings.length }} current job pairings</summary>
            <ul class="pairings">
              <li v-for="p in cv.pairings" :key="p.id">
                <button
                  class="text-button"
                  @click="openJob(jobs.find((j) => j.id === p.id))"
                >
                  {{ p.company }} · {{ p.title }}</button
                ><span class="stage-badge" :data-stage="p.status">{{
                  stageLabel(p.status)
                }}</span>
              </li>
            </ul>
          </details>
          <details>
            <summary>Supported candidate evidence</summary>
            <dl>
              <template v-for="e in cv.evidence" :key="e.item"
                ><dt>{{ e.item }}</dt>
                <dd>{{ e.evidence }}</dd></template
              >
            </dl>
          </details>
        </section>
        <div v-if="!cvs.length" class="empty-state">
          <h2>CV library unavailable</h2>
          <p>Reconnect and refresh to load current files and pairings.</p>
        </div>
      </template>
      <template v-else
        ><section class="panel" v-if="profile">
          <h2 class="section-title">
            <UserRound :size="22" aria-hidden="true" />{{ profile.name }}
          </h2>
          <div class="profile-grid">
            <div>
              <h3 class="section-title">
                <Mail :size="18" aria-hidden="true" />Contact
              </h3>
              <p>{{ profile.email }}</p>
              <p class="mono">{{ profile.phone }}</p>
              <a
                :href="safeLink(profile.github)"
                target="_blank"
                rel="noopener noreferrer"
                >{{ profile.github }}</a
              >
              <p>Addis Ababa, Ethiopia · UTC+3</p>
            </div>
            <div>
              <h3 class="section-title">
                <GraduationCap :size="19" aria-hidden="true" />Education &
                credentials
              </h3>
              <p>{{ profile.degree }}</p>
              <p class="credential-line">
                <ShieldCheck :size="20" aria-hidden="true" /><span>{{
                  profile.certification
                }}</span>
              </p>
            </div>
          </div>
          <dl>
            <template v-for="p in profile.projects" :key="p.item"
              ><dt>{{ p.item }}</dt>
              <dd>{{ p.evidence }}</dd></template
            >
          </dl>
          <p class="context-note">
            This profile supplies new materials. Historical applications retain
            their original content. Edit verified facts in the local candidate
            profile file.
          </p>
        </section>
        <section class="panel">
          <h2>Workspace settings</h2>
          <p>
            Local access only. Credentials and raw discovery state are excluded
            from file serving.
          </p>
          <p class="context-note">
            Dates use Africa/Addis_Ababa (UTC+3). Legacy display dates are not
            treated as verified publication timestamps.
          </p>
          <button
            @click="
              reload();
              ancillary();
            "
          >
            Reload connections & data
          </button>
        </section>
        <section class="panel">
          <h2>
            Blacklist
            <span class="subtle"
              >{{ jobs.filter((j) => j.status === "deleted").length }} preserved
              records</span
            >
          </h2>
          <p>
            Deleted leads stay blacklisted by ID, URL, and employer/title
            signature. This inspector is read-only.
          </p>
          <label
            >Search blacklist<input
              v-model="blacklistQuery"
              type="search"
              placeholder="Employer, title, or reason"
          /></label>
          <div class="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Role / employer</th>
                  <th>Deleted</th>
                  <th>Reason</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="job in blacklist" :key="job.id">
                  <td>
                    <strong>{{ job.title }}</strong
                    ><br />{{ job.company
                    }}<small class="break-url">{{ job.applyUrl }}</small>
                  </td>
                  <td class="mono">{{ displayDate(job.deletedAt) }}</td>
                  <td>
                    {{
                      job.deletionReason ||
                      "Reason not recorded in legacy record"
                    }}
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </section></template
      >
      <footer class="page-footer">
        Roletrail · Evidence before action
        <span>Local files · Private workspace</span>
      </footer>
    </main>
    <JobRoletrail
      :job="selected"
      :cvs="cvs"
      :offline="offline"
      :busy="busy"
      :error="error"
      :notice="notice"
      :mutate="mutate"
      @close="closeJob"
      @recheck="recheck"
      @error="error = $event"
      @notice="notice = $event"
    />
  </div>
</template>
