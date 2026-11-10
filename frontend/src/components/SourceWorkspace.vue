<script setup>
import { ref, computed } from "vue";
import { Radar, ChevronRight, Send, Mail, LoaderCircle } from "@lucide/vue";
import StatusBadge from "./StatusBadge.vue";
import { displayDate } from "../domain/jobs.mjs";
const props = defineProps(["integrations", "searches", "offline", "api"]);
const emit = defineEmits(["refresh", "error", "notice", "open"]);
const query = ref(""),
  sources = ref(["ats", "jobspy", "telegram", "feeds", "pages", "people"]),
  dryRun = ref(false),
  busy = ref("");
const expanded = ref(new Set());
const activeRun = computed(() =>
  props.searches.find((r) => ["running", "cancelling"].includes(r.state)),
);
async function start() {
  if (!sources.value.length)
    return emit("error", "Choose at least one source.");
  busy.value = "search";
  try {
    await props.api("/api/searches", {
      sources: sources.value,
      query: query.value,
      dryRun: dryRun.value,
    });
    emit("notice", "Search started. Progress is saved locally.");
    emit("refresh");
  } catch (e) {
    emit("error", e.message);
  } finally {
    busy.value = "";
  }
}
async function cancel() {
  try {
    await props.api("/api/searches/cancel", { id: activeRun.value.id });
    emit(
      "notice",
      "Cancellation requested. The current provider call may need to finish first.",
    );
    emit("refresh");
  } catch (e) {
    emit("error", e.message);
  }
}
async function reviewed(id) {
  try {
    await props.api("/api/email/review", { reviewId: id });
    emit("refresh");
  } catch (e) {
    emit("error", e.message);
  }
}
async function checkEmails() {
  busy.value = "email";
  try {
    const r = await props.api("/api/check-emails", {});
    emit(
      "notice",
      r.status === "unconfigured"
        ? "No email account configured."
        : r.status === "partial"
          ? `Some accounts failed; ${r.updates?.length || 0} updates saved. Review details.`
          : `Email check complete: ${r.updates?.length || 0} updates, ${r.suggestions?.length || 0} items need review.`,
    );
  } catch (e) {
    emit("error", e.message);
  } finally {
    emit("refresh");
    busy.value = "";
  }
}
</script>
<template>
  <div class="two-columns">
    <section class="panel">
      <div class="panel-heading">
        <h2>Start a search</h2>
        <span class="subtle">Official evidence required</span>
      </div>
      <p>
        Software development, Power Platform and aligned automation roles.
        Remote from Ethiopia, local Addis Ababa, or reviewed overseas sponsorship.
      </p>
      <label
        >Optional search phrase<input
          v-model="query"
          maxlength="300"
          placeholder="Use the configured search matrix"
      /></label>
      <fieldset>
        <legend>Sources</legend>
        <label
          class="checkbox"
          v-for="[id, label] in [
            ['ats', 'Direct ATS & employer portals'],
            ['jobspy', 'Indeed, Glassdoor & ZipRecruiter'],
            ['telegram', 'Telegram channels'],
            ['feeds', 'Job feeds'],
            ['pages', 'Discovery pages'],
            ['people', 'LinkedIn hiring posts · individuals & recruiters'],
          ]"
          :key="id"
          ><input type="checkbox" v-model="sources" :value="id" />{{
            label
          }}</label
        >
      </fieldset>
      <p class="context-note">
        Software engineering and development roles are welcome. Power Platform
        and Dynamics 365 are preferred, not required.
      </p>
      <p class="context-note">
        Google Jobs is unavailable in the installed adapter and is reported as a
        coverage gap.
      </p>
      <label class="checkbox"
        ><input type="checkbox" v-model="dryRun" />Preview only — don’t add
        verified jobs</label
      ><button
        class="primary"
        :disabled="offline || !!activeRun || !!busy"
        @click="start"
      >
        <component
          :is="busy === 'search' ? LoaderCircle : Radar"
          :size="17"
          :class="{ 'is-spinning': busy === 'search' }"
          aria-hidden="true"
        />{{ activeRun ? "Search in progress" : "Find matching jobs" }}
      </button>
      <p class="context-note">
        Core fit: 0–3 years; reviewed 3–5-year stretches. Prefer postings within 30 days; older roles require a current open application. Keep location/work-right checks,
        accurate materials, and preserved blacklists. Individual LinkedIn hiring posts are discovery leads; author and application details require review.
      </p>
    </section>
    <section class="panel">
      <h2>Connections</h2>
      <div class="connection-item">
        <div>
          <h3 class="connection-title">
            <Send :size="18" aria-hidden="true" />Telegram
          </h3>
          <StatusBadge :state="integrations?.telegram?.state || 'unknown'" />
          <p class="context-note">
            {{
              integrations?.telegram?.channelReadVerified
                ? "Channel read verified during setup. Searches check current access."
                : "A saved session does not establish current channel access."
            }}
          </p>
        </div>
        <span class="connection-mode">{{
          integrations?.telegram?.configuredAdapter === "mtproto"
            ? "Account session"
            : integrations?.telegram?.configuredAdapter === "preview"
              ? "Public preview"
              : "Adapter unavailable"
        }}</span>
      </div>
      <p class="context-note">
        Repair locally with <code>python connect_telegram.py</code>. Credentials
        stay encrypted on this computer.
      </p>
      <div class="connection-item">
        <div>
          <h3 class="connection-title">
            <Mail :size="18" aria-hidden="true" />Email
          </h3>
          <StatusBadge
            :state="integrations?.email?.status || 'not_checked'"
            :label="integrations?.email?.status === 'ok' ? 'Checked' : ''"
          />
          <p class="date mono">
            {{
              integrations?.email?.checkedAt
                ? "Last checked " + displayDate(integrations.email.checkedAt)
                : "No check recorded yet"
            }}
          </p>
        </div>
        <button :disabled="offline || !!busy" @click="checkEmails">
          <component
            :is="busy === 'email' ? LoaderCircle : Mail"
            :size="16"
            :class="{ 'is-spinning': busy === 'email' }"
            aria-hidden="true"
          />
          {{ busy === "email" ? "Checking…" : "Check email" }}
        </button>
      </div>
      <p class="context-note">
        Read-only mailbox checks. Uncertain messages need review; an email does
        not establish which kit was submitted.
      </p>
      <ul class="plain-list connection-accounts">
        <li v-for="a in integrations?.email?.accounts || []" :key="a.account">
          <span class="account-name">{{ a.account }}</span
          ><StatusBadge :state="a.state" />
          <span v-if="a.reason">· {{ a.reason }}</span>
        </li>
      </ul>
      <details v-if="integrations?.email?.suggestions?.length">
        <summary>
          {{ integrations.email.suggestions.length }} email items need review
        </summary>
        <article
          v-for="(s, i) in integrations.email.suggestions"
          :key="i"
          class="review-item"
        >
          <p>{{ s.subject }}</p>
          <p class="context-note">{{ s.reason }}</p>
          <button v-if="s.jobId" @click="emit('open', s.jobId)">
            Review application
          </button>
          <p v-else class="context-note">
            Find the matching application and record the outcome explicitly.
          </p>
          <button
            v-if="s.reviewId"
            :disabled="offline"
            @click="reviewed(s.reviewId)"
          >
            Mark reviewed
          </button>
        </article>
      </details>
    </section>
  </div>
  <section class="panel" v-if="activeRun">
    <div class="panel-heading">
      <h2>Search {{ activeRun.state }}</h2>
      <button
        :disabled="offline || activeRun.state === 'cancelling'"
        @click="cancel"
      >
        Request cancellation
      </button>
    </div>
    <progress aria-label="Search in progress"></progress>
    <p class="mono">
      {{ activeRun.progress?.funnel?.fetched || 0 }} fetched ·
      {{ activeRun.progress?.funnel?.verified || 0 }} verified
    </p>
    <p class="context-note">
      Refresh safely to recover progress. Cancellation waits for the active
      provider call.
    </p>
  </section>
  <section class="panel">
    <div class="panel-heading">
      <h2>Search history & coverage</h2>
      <span class="subtle">Last {{ searches.length }} runs</span>
    </div>
    <p v-if="!searches.length">
      No dashboard searches recorded yet. Earlier CLI reports remain in the
      local discovery store.
    </p>
    <details
      v-for="run in searches"
      :key="run.id"
      class="run-record"
      :open="run.id === activeRun?.id"
      @toggle="
        (e) => (e.target.open ? expanded.add(run.id) : expanded.delete(run.id))
      "
    >
      <summary>
        <ChevronRight class="run-chevron" :size="16" aria-hidden="true" /><span
          class="run-status"
          ><StatusBadge :state="run.state" /></span
        ><span class="mono">{{ displayDate(run.startedAt) }}</span
        ><span
          >{{ run.progress?.added || 0 }} added /
          {{ run.progress?.verified || 0 }} verified</span
        ><span>{{ run.params?.sync ? "Add verified jobs" : "Preview" }}</span>
      </summary>
      <template v-if="expanded.has(run.id) || run.id === activeRun?.id">
        <p v-if="run.error" class="error-text">{{ run.error }}</p>
        <div class="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Source</th>
                <th>State</th>
                <th>Details</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(s, i) in run.progress?.sources || []" :key="i">
                <td>{{ s.source }}</td>
                <td><StatusBadge :state="s.state" /></td>
                <td>
                  {{
                    s.reason ||
                    (s.fetched !== undefined
                      ? `${s.fetched} fetched`
                      : "Checked")
                  }}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <details v-if="Object.keys(run.progress?.reasons || {}).length">
          <summary>Why leads were held or rejected</summary>
          <ul>
            <li v-for="(count, reason) in run.progress.reasons" :key="reason">
              {{ reason.replaceAll("_", " ") }} · {{ count }}
            </li>
          </ul>
        </details>
      </template>
    </details>
  </section>
  <section class="panel">
    <h2>Evidence queue</h2>
    <p>
      {{ integrations?.evidenceTasks?.states?.needs_evidence || 0 }} internal
      leads need more evidence. Incomplete records stay outside your opportunity
      list.
    </p>
    <ul class="plain-list">
      <li
        v-for="(count, reason) in integrations?.evidenceTasks?.reasons || {}"
        :key="reason"
      >
        {{ reason.replaceAll("_", " ") }} <span class="mono">{{ count }}</span>
      </li>
    </ul>
    <p class="context-note">
      The selected source families retry pending evidence in the next search.
    </p>
  </section>
</template>
