<template>
  <main class="blocker-layout">
    <section :class="['blocker-card', tone]" :role="tone === 'fatal' ? 'alert' : 'status'" aria-live="assertive">
      <span class="blocker-code">{{ code }}</span><p class="page-kicker">DESKTOP STARTUP</p>
      <h1>{{ title }}</h1><p>{{ message }}</p><slot />
    </section>
  </main>
</template>
<script setup lang="ts">defineProps<{ code: string; title: string; message: string; tone: 'neutral' | 'warning' | 'fatal' }>()</script>
