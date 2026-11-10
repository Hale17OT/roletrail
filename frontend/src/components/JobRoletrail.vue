<script setup>
import { ref, watch, nextTick } from "vue";
import RoletrailSelect from "./RoletrailSelect.vue";
import {
  X,
  ArrowUpRight,
  Copy,
  Save,
  RotateCw,
  Trash2,
  Send,
  Bookmark,
  ArrowRight,
} from "@lucide/vue";
import { eligible, safeLink, displayDate, stageLabel } from "../domain/jobs.mjs";
const props = defineProps([
  "job",
  "cvs",
  "offline",
  "busy",
  "error",
  "notice",
  "mutate",
]);
const emit = defineEmits(["close", "recheck", "error", "notice"]);
const detail = ref(null),
  confirmation = ref(null),
  tab = ref("overview"),
  pending = ref(""),
  reason = ref("");
const note = ref(""),
  actionText = ref(""),
  actionDue = ref(""),
  letter = ref(""),
  cvFile = ref(""),
  kitRevision = ref("");
function reloadKit() {
  letter.value = props.job.tailoredLetter || "";
  cvFile.value = props.job.matchedCv?.fileName || "";
  kitRevision.value = props.job._meta?.revision || "";
}
watch(
  () => props.job,
  async (j, old) => {
    if (j) {
      if (j.id !== old?.id) {
        tab.value = "overview";
        note.value = "";
      }
      const dirty =
        j.id === old?.id &&
        (letter.value !== old.tailoredLetter ||
          cvFile.value !== old.matchedCv?.fileName);
      if (!dirty) reloadKit();
      else
        emit(
          "notice",
          "Your unsaved kit draft is preserved. Reload saved materials before replacing a newer revision.",
        );
      actionText.value = j.nextAction?.text || "";
      actionDue.value = j.nextAction?.due || "";
    }
    await nextTick();
    if (j && !detail.value.open) detail.value.showModal();
    else if (!j && detail.value?.open) detail.value.close();
  },
  { immediate: true },
);
async function stage(s) {
  if (
    s === "applied" &&
    (letter.value !== props.job.tailoredLetter ||
      cvFile.value !== props.job.matchedCv?.fileName)
  ) {
    emit(
      "error",
      "Save the kit before recording a submission. The saved materials are the ones preserved.",
    );
    return;
  }
  if (["deleted", "applied", "declined", "rejected", "rebuild"].includes(s)) {
    pending.value = s;
    reason.value = "";
    await nextTick();
    confirmation.value.showModal();
    confirmation.value.querySelector("button")?.focus();
  } else props.mutate({ status: s });
}
async function confirm() {
  if (
    await props.mutate({
      revision: kitRevision.value,
      ...(pending.value === "rebuild"
        ? { rebuildKit: true }
        : { status: pending.value }),
      confirmSubmission: pending.value === "applied",
      reason: reason.value,
    })
  ) {
    confirmation.value.close();
    pending.value = "";
    await nextTick();
    detail.value?.querySelector(".close-button")?.focus();
  }
}
async function copy(text) {
  try {
    await navigator.clipboard.writeText(text);
    emit("notice", "Copied to clipboard.");
  } catch {
    emit("error", "Clipboard unavailable. Select the text and copy manually.");
  }
}
async function saveNote() {
  if (await props.mutate({ note: note.value })) {
    note.value = "";
    await nextTick();
    detail.value?.querySelector('textarea[maxlength="5000"]')?.focus();
  }
}
</script>
<template>
  <dialog
    ref="detail"
    class="dossier-dialog"
    aria-labelledby="dossier-title"
    @cancel.prevent="emit('close')"
    @click="
      (e) => {
        if (e.target === detail) emit('close');
      }
    "
  >
    <template v-if="job"
      ><header class="dossier-header">
        <div>
          <p class="company">{{ job.company }}</p>
          <h2 id="dossier-title">{{ job.title }}</h2>
        </div>
        <button
          class="close-button"
          autofocus
          @click="emit('close')"
          aria-label="Close dossier"
        >
          <X :size="18" aria-hidden="true" />
        </button>
      </header>
      <div class="dossier-summary">
        <span class="stage-badge" :data-stage="job.status">{{
          stageLabel(job.status)
        }}</span
        ><span :class="{ verified: eligible(job) }">{{
          job._meta?.eligibility?.label
        }}</span
        ><span class="mono">Internal fit {{ job.matchScore }} / 100</span>
      </div>
      <nav class="dossier-tabs" aria-label="Roletrail sections">
        <button
          v-for="[id, label] in [
            ['overview', 'Overview'],
            ['fit', 'Fit & evidence'],
            ['kit', 'Application kit'],
            ['activity', 'Activity'],
          ]"
          :key="id"
          :class="{ active: tab === id }"
          :aria-current="tab === id ? 'page' : undefined"
          @click="tab = id"
        >
          {{ label }}
        </button>
      </nav>
      <div class="dossier-body">
        <div class="feedback error" v-if="error" role="alert">{{ error }}</div>
        <div class="feedback" v-if="notice" role="status">{{ notice }}</div>
        <template v-if="tab === 'overview'"
          ><dl class="facts-grid">
            <div>
              <dt>Location & arrangement</dt>
              <dd>{{ job.region }}<br />{{ job.locationType }}</dd>
            </div>
            <div>
              <dt>Compensation</dt>
              <dd class="mono">
                {{ job.salary }}<br /><span class="subtle">{{
                  job.payoutMethod
                }}</span>
              </dd>
            </div>
            <div>
              <dt>Publication evidence</dt>
              <dd>
                {{
                  job._meta?.eligibility?.publishedAt
                    ? displayDate(job._meta.eligibility.publishedAt)
                    : "Publication date not verified"
                }}<br /><span class="subtle"
                  >Legacy display: {{ job.postedDate }} ·
                  {{ job.postedAgo }}</span
                >
              </dd>
            </div>
            <div>
              <dt>Current source check</dt>
              <dd class="mono">
                {{ displayDate(job._meta?.eligibility?.auditedAt) }}
              </dd>
            </div>
          </dl>
          <h3>Recorded source</h3>
          <p class="break-url">
            {{ job._meta?.eligibility?.sourceUrl || job.applyUrl }}
          </p>
          <h3>Position overview</h3>
          <p class="preserve-lines">{{ job.description }}</p>
          <h3>Technical focus</h3>
          <div class="tags">
            <span v-for="t in job.tags" :key="t" :data-skill="t">{{ t }}</span>
          </div>
          <h3>Application instructions</h3>
          <p class="preserve-lines">{{ job.instructions }}</p></template
        >
        <template v-else-if="tab === 'fit'"
          ><h3>Eligibility & official-source evidence</h3>
          <p>{{ job._meta?.eligibility?.label }}</p>
          <ul>
            <li v-for="r in job._meta?.eligibility?.reasons" :key="r">
              {{ r.replaceAll("_", " ") }}
            </li>
          </ul>
          <dl>
            <template
              v-for="(v, k) in job._meta?.eligibility?.checks || {}"
              :key="k"
              ><dt>{{ k }}</dt>
              <dd>
                {{
                  typeof v === "boolean"
                    ? v
                      ? "Confirmed by source check"
                      : "Not established"
                    : v
                }}
              </dd></template
            >
          </dl>
          <button :disabled="offline || !!busy" @click="emit('recheck')">
            {{ busy === job.id ? "Checking…" : "Recheck official source" }}
          </button>
          <h3>Fit rationale</h3>
          <p class="preserve-lines">{{ job.fitRationale }}</p>
          <h3>Requirement mapping</h3>
          <p class="context-note">
            Candidate evidence is a mapping, not confirmation that every
            employer requirement is met. Review historical claims against your
            actual work.
          </p>
          <dl>
            <template v-for="r in job.requirements" :key="r.item"
              ><dt>{{ r.item }}</dt>
              <dd>{{ r.evidence }}</dd></template
            >
          </dl>
          <h3>How the score is used</h3>
          <p>
            {{
              job._meta?.eligibility?.rankingMethod ||
              "Legacy internal assessment; calculation provenance was not recorded."
            }}. This score does not establish eligibility or hiring probability.
          </p></template
        >
        <template v-else-if="tab === 'kit'"
          ><p
            class="review-notice"
            v-if="job._meta?.protected && !job.submittedKit"
          >
            Historical application: the submitted CV version and letter were not
            recorded. The current draft cannot establish what was submitted.
          </p>
          <p
            class="review-notice"
            v-if="!job._meta?.protected && !job.sourceEvidence"
          >
            Legacy draft: contact details and achievement claims may differ from
            the canonical profile. Recheck the official source, then rebuild the
            draft from verified facts.
          </p>
          <button
            v-if="!job._meta?.protected"
            :disabled="offline || !!busy"
            @click="stage('rebuild')"
          >
            Rebuild draft from verified facts
          </button>
          <section v-if="job.submittedKit" class="submitted-kit">
            <h3>Confirmed submitted kit</h3>
            <p class="mono">
              {{ displayDate(job.submittedKit.recordedAt) }} ·
              {{ job.submittedKit.cvSha256.slice(0, 12) }}
            </p>
            <a
              class="button"
              :href="job.submittedKit.cvUrl"
              target="_blank"
              rel="noopener noreferrer"
              >Open preserved submitted CV
              <ArrowUpRight :size="16" aria-hidden="true"
            /></a>
            <details>
              <summary>Submitted letter</summary>
              <p class="preserve-lines">{{ job.submittedKit.letter }}</p>
            </details>
          </section>
          <h3>
            {{
              job._meta?.protected
                ? "Historical draft materials"
                : "Application materials"
            }}
          </h3>
          <RoletrailSelect
            v-model="cvFile"
            label="Paired CV"
            :disabled="job._meta?.protected || offline"
            :options="
              cvs.map((cv) => ({
                value: cv.fileName,
                label: cv.shortLabel + ' · ' + cv.version,
                disabled: !cv.available,
              }))
            "
          />
          <p class="context-note">{{ job.matchedCv?.reason }}</p>
          <p class="context-note">
            Review the CV’s actual achievements before submitting. A file
            fingerprint identifies its version; it does not validate its claims.
          </p>
          <a
            v-if="cvFile"
            class="button"
            :href="'/cvs/' + encodeURIComponent(cvFile)"
            target="_blank"
            rel="noopener noreferrer"
            >Open current PDF <ArrowUpRight :size="16" aria-hidden="true"
          /></a>
          <h3>Pitch</h3>
          <p class="preserve-lines">{{ job.keyPitch }}</p>
          <button @click="copy(job.keyPitch)">
            <Copy :size="16" aria-hidden="true" />Copy pitch</button
          ><label class="letter-label"
            >Cover letter<textarea
              v-model="letter"
              rows="18"
              :readonly="job._meta?.protected"
              :disabled="offline"
            />
          </label>
          <div class="button-row">
            <button @click="copy(letter)">
              <Copy :size="16" aria-hidden="true" />Copy letter</button
            ><button
              class="primary"
              v-if="!job._meta?.protected"
              :disabled="offline || !!busy"
              @click="
                mutate({ revision: kitRevision, letter, cvFileName: cvFile })
              "
            >
              <Save :size="16" aria-hidden="true" />Save application kit
            </button>
            <button v-if="!job._meta?.protected" @click="reloadKit">
              <RotateCw :size="16" aria-hidden="true" />Reload saved kit
            </button>
          </div>
          <h3>Application instructions</h3>
          <p class="preserve-lines">{{ job.instructions }}</p></template
        >
        <template v-else
          ><h3>Next action</h3>
          <label
            >Action<input
              v-model="actionText"
              maxlength="500"
              placeholder="e.g. Follow up with the recruiter"
              :disabled="offline" /></label
          ><label
            >Due date (optional)<input
              type="date"
              v-model="actionDue"
              :disabled="offline" /></label
          ><button
            class="primary"
            :disabled="offline || !!busy"
            @click="
              mutate({ nextAction: { text: actionText, due: actionDue } })
            "
          >
            <Save :size="16" aria-hidden="true" />Save next action
          </button>
          <h3>Add a note</h3>
          <label
            >Private application note<textarea
              v-model="note"
              rows="3"
              maxlength="5000"
              :disabled="offline"
            /></label
          ><button
            class="primary"
            :disabled="offline || !!busy || !note.trim()"
            @click="saveNote"
          >
            <Save :size="16" aria-hidden="true" />Save note
          </button>
          <h3>Recorded activity</h3>
          <p class="context-note" v-if="!job.activity?.length">
            No event history was recorded for this legacy entry. Its current
            stage is preserved; past dates have not been invented.
          </p>
          <ol class="activity-list">
            <li
              v-for="(event, i) in [...(job.activity || [])].reverse()"
              :key="i"
            >
              <span class="date mono">{{ displayDate(event.at) }}</span
              ><strong>{{ event.type.replaceAll("_", " ") }}</strong>
              <p v-if="event.toStatus">
                {{ stageLabel(event.fromStatus) }} → {{ stageLabel(event.toStatus) }}
              </p>
              <p class="preserve-lines">{{ event.text || event.reason }}</p>
              <p class="mono" v-if="event.due">Due {{ event.due }}</p>
              <details v-if="event.previousKit">
                <summary>Previous draft materials</summary>
                <p class="mono">
                  {{
                    event.previousKit.matchedCv?.fileName || "CV not recorded"
                  }}
                </p>
                <p class="preserve-lines">{{ event.previousKit.keyPitch }}</p>
                <details>
                  <summary>Previous cover letter</summary>
                  <p class="preserve-lines">
                    {{ event.previousKit.tailoredLetter }}
                  </p>
                </details>
              </details>
            </li>
          </ol></template
        >
      </div>
      <footer class="dossier-actions">
        <div class="button-row">
          <a
            v-if="eligible(job) && !offline && safeLink(job.applyUrl)"
            class="button primary"
            :href="'/api/jobs/apply?id=' + encodeURIComponent(job.id)"
            target="_blank"
            rel="noopener noreferrer"
            >Open official application
            <ArrowUpRight :size="16" aria-hidden="true" /></a
          ><span v-else class="context-note">{{
            offline
              ? "Reconnect to take action."
              : job._meta?.protected
                ? "Application history preserved."
                : "Verify eligibility before applying."
          }}</span
          ><button :disabled="offline || !!busy" @click="emit('recheck')">
            <RotateCw :size="16" aria-hidden="true" />Recheck source
          </button>
        </div>
        <div class="button-row">
          <button
            v-for="s in job._meta?.actions || []"
            :key="s"
            class="stage-action"
            :data-action="s"
            :class="{ danger: s === 'deleted', primary: s === 'applied' }"
            :disabled="
              offline ||
              !!busy ||
              (['ready', 'applied'].includes(s) && !eligible(job))
            "
            @click="stage(s)"
          >
            <component
              :is="
                s === 'deleted'
                  ? Trash2
                  : s === 'applied'
                    ? Send
                    : s === 'saved'
                      ? Bookmark
                      : ArrowRight
              "
              :size="16"
              aria-hidden="true"
            />
            {{
              s === "deleted"
                ? "Delete & blacklist"
                : s === "applied"
                  ? "Record submitted"
                  : s === "saved"
                    ? "Save"
                    : s === "ready"
                      ? "Mark ready"
                      : s==='rejected' ? 'Record employer rejection' : s==='declined' ? 'Withdraw / decline' : `Mark ${s}`
            }}
          </button>
        </div>
      </footer></template
    >
  </dialog>
  <dialog
    ref="confirmation"
    class="confirm-dialog"
    aria-labelledby="confirm-title"
    @cancel="pending = ''"
  >
    <h2 id="confirm-title">
      {{
        pending === "deleted"
          ? "Delete and blacklist this role?"
          : pending === "applied"
            ? "Confirm an actual submission"
            : pending === "rebuild"
              ? "Rebuild the application draft?"
              : pending === 'rejected' ? 'Record an employer rejection?' : 'Record your withdrawal / decline?'
      }}
    </h2>
    <p v-if="pending === 'deleted'">
      This role will leave your active list and remain on the scraper blacklist.
      It cannot be restored from this dashboard.
    </p>
    <p v-else-if="pending === 'applied'">
      Confirm that you submitted this application using the saved CV and letter.
      Those materials will be preserved. Opening the employer’s page alone does
      not count as submission.
    </p>
    <p v-else-if="pending === 'rebuild'">
      Use the verified posting and canonical profile to replace the draft
      letter, pitch, and CV pairing. Previous draft materials remain in the
      recorded activity. Submitted applications cannot be rebuilt.
    </p>
    <p v-else-if="pending==='rejected'">Record that the employer rejected this application. Your application history and submitted materials will be preserved.</p>
    <p v-else>This records your decision to withdraw or decline. Use employer rejection when the employer turned down your application.</p>
    <label v-if="!['applied', 'rebuild'].includes(pending)"
      >Reason (optional)<textarea v-model="reason" rows="3" maxlength="1000" />
    </label>
    <p v-if="error" class="error-text" role="alert">{{ error }}</p>
    <div class="button-row">
      <button
        @click="
          confirmation.close();
          pending = '';
        "
      >
        Cancel</button
      ><button
        :class="pending === 'deleted' ? 'danger' : 'primary'"
        :disabled="!!busy"
        @click="confirm"
      >
        <Trash2 v-if="pending === 'deleted'" :size="16" aria-hidden="true" />
        {{ pending === "applied" ? "Yes, I submitted this kit" : "Confirm" }}
      </button>
    </div>
  </dialog>
</template>
