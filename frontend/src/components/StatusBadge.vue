<script setup>
import { computed } from "vue";
import {
  CircleCheck,
  TriangleAlert,
  Clock3,
  LoaderCircle,
  Info,
} from "@lucide/vue";
const props = defineProps({
  state: { type: String, default: "unknown" },
  label: { type: String, default: "" },
});
const styles = {
  connected: ["Connected", "success", CircleCheck],
  verified: ["Verified", "success", CircleCheck],
  ok: ["OK", "success", CircleCheck],
  completed: ["Finished", "info", CircleCheck],
  running: ["Running", "info", LoaderCircle],
  cancelling: ["Cancelling", "warning", LoaderCircle],
  partial: ["Needs attention", "warning", TriangleAlert],
  incomplete: ["Incomplete", "warning", TriangleAlert],
  needs_adapter: ["Needs adapter", "warning", TriangleAlert],
  needs_evidence: ["Needs evidence", "warning", Info],
  error: ["Error", "danger", TriangleAlert],
  failed: ["Failed", "danger", TriangleAlert],
  interrupted: ["Interrupted", "warning", TriangleAlert],
  unavailable: ["Unavailable", "muted", Info],
  unsupported: ["Unsupported", "muted", Info],
  unconfigured: ["Not configured", "muted", Info],
  not_checked: ["Not checked", "muted", Clock3],
  not_run: ["Not run", "muted", Clock3],
  cancelled: ["Cancelled", "muted", Info],
  unknown: ["Unknown", "muted", Info],
};
const appearance = computed(
  () =>
    styles[props.state] || [props.state.replaceAll("_", " "), "muted", Info],
);
</script>
<template>
  <span
    class="status-badge"
    :data-tone="appearance[1]"
    :data-busy="['running', 'cancelling'].includes(state)"
    ><component :is="appearance[2]" :size="14" aria-hidden="true" /><span>{{
      label || appearance[0]
    }}</span></span
  >
</template>
