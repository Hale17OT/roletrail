<script setup>
import { safeLink, displayDate } from '../domain/jobs.mjs';
defineProps({ report: Object });
</script>

<template>
  <details v-if="report" class="opportunity-audit">
    <summary>
      <span><strong>Opportunity audit</strong> · {{ displayDate(report.finishedAt) }}</span>
      <span class="audit-counts">{{ report.reviewed }} checked · {{ report.counts.verified || 0 }} verified · {{ report.counts.removed || 0 }} excluded · {{ report.counts.held || 0 }} awaiting evidence</span>
    </summary>
    <p v-if="report.scopeNote">{{ report.scopeNote }}</p>
    <p v-if="report.restoredIds?.length">{{ report.restoredIds.length }} roles returned to review after your preference clarification.</p>
    <p>Excluded roles stay in your blacklist with their evidence and application materials. Unconfirmed age or experience removes a role. Other source or application checks can remain under review. Your application history is preserved.</p>
    <p v-if="report.dryRun">Preview only: exclusions have not been applied.</p>
    <div v-for="job in report.jobs" :key="job.id" class="audit-row">
      <div class="audit-role"><strong>{{ job.company }} · {{ job.title }}</strong><span class="eyebrow">{{ job.decision === 'removed' ? 'Excluded' : job.decision === 'verified' ? 'Verified' : 'Awaiting evidence' }}</span></div>
      <ul v-if="job.violations.length || job.unverified.length">
        <li v-for="(finding, i) in [...job.violations, ...job.unverified]" :key="i">
          <strong>Rule {{ finding.rule }}</strong> · {{ job.violations.includes(finding) ? 'Confirmed' : 'Unverified' }}: {{ finding.detail }}
        </li>
      </ul>
      <div class="audit-sources"><a v-for="(source, i) in job.sources.filter(s => safeLink(s.url))" :key="i" :href="safeLink(source.postingUrl || source.url)" target="_blank" rel="noopener noreferrer">Evidence {{ i + 1 }} · {{ source.authority === 'official' ? 'Employer / ATS' : 'Linked listing' }} ↗</a></div>
    </div>
  </details>
</template>
