<script setup>
// shadcn-vue composition, with the existing Roletrail tokens rather than a second theme.
import { ref } from "vue";
import {
  SelectRoot,
  SelectTrigger,
  SelectValue,
  SelectIcon,
  SelectPortal,
  SelectContent,
  SelectViewport,
  SelectItem,
  SelectItemText,
  SelectItemIndicator,
  SelectScrollUpButton,
  SelectScrollDownButton,
} from "reka-ui";
import { Check, ChevronDown, ChevronUp } from "@lucide/vue";
const props = defineProps({
  modelValue: { type: String, default: "" },
  options: { type: Array, required: true },
  label: { type: String, required: true },
  disabled: Boolean,
});
const emit = defineEmits(["update:modelValue"]);
const host = ref(null),
  portalTarget = ref(undefined),
  isOpen = ref(false);
const ALL = "__dossier_all__";
function opening(open) {
  if (open) portalTarget.value = host.value?.closest("dialog") || undefined;
}
function escape(event) {
  event.preventDefault();
  event.stopPropagation();
  isOpen.value = false;
}
</script>
<template>
  <div ref="host" class="select-field">
    <span class="select-label">{{ label }}</span>
    <SelectRoot
      v-model:open="isOpen"
      :model-value="modelValue || ALL"
      :disabled="disabled"
      @update:model-value="
        (value) => emit('update:modelValue', value === ALL ? '' : value)
      "
      @update:open="opening"
    >
      <SelectTrigger
        class="select-trigger"
        :class="{ 'is-selected': modelValue !== '' && modelValue !== 'recent' }"
        :aria-label="label"
        ><SelectValue /><SelectIcon class="select-chevron"
          ><ChevronDown :size="16" aria-hidden="true" /></SelectIcon
      ></SelectTrigger>
      <SelectPortal :to="portalTarget">
        <SelectContent
          class="select-content"
          position="popper"
          align="start"
          :side-offset="8"
          :collision-padding="12"
          :collision-boundary="portalTarget"
          :body-lock="false"
          :avoid-collisions="true"
          @escape-key-down="escape"
        >
          <SelectScrollUpButton class="select-scroll"
            ><ChevronUp :size="16"
          /></SelectScrollUpButton>
          <SelectViewport class="select-viewport">
            <SelectItem
              v-for="option in options"
              :key="option.value || ALL"
              class="select-item"
              :value="option.value || ALL"
              :disabled="option.disabled"
              :text-value="option.label"
              ><SelectItemText>{{ option.label }}</SelectItemText
              ><SelectItemIndicator class="select-check"
                ><Check :size="16" aria-hidden="true" /></SelectItemIndicator
            ></SelectItem>
          </SelectViewport>
          <SelectScrollDownButton class="select-scroll"
            ><ChevronDown :size="16"
          /></SelectScrollDownButton>
        </SelectContent>
      </SelectPortal>
    </SelectRoot>
  </div>
</template>
