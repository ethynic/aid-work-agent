<template>
  <!-- QQ邮箱：企鹅角标信封 -->
  <svg v-if="brand === 'qq'" :width="size" :height="size" viewBox="0 0 40 40" aria-hidden="true" focusable="false">
    <rect x="1" y="1" width="38" height="38" rx="9.5" fill="#38B6F0" />
    <rect x="6.5" y="10.5" width="27" height="19.5" rx="3" fill="#fff" />
    <path d="M8.2 13 20 21.6 31.8 13" fill="none" stroke="#38B6F0" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" />
    <circle cx="31.5" cy="29.5" r="7" fill="#fff" stroke="#38B6F0" />
    <ellipse cx="31.5" cy="29.9" rx="3.7" ry="4.3" fill="#262626" />
    <ellipse cx="31.5" cy="32" rx="2.1" ry="1.8" fill="#fff" />
    <circle cx="30.4" cy="28.1" r=".85" fill="#fff" /><circle cx="32.6" cy="28.1" r=".85" fill="#fff" />
    <circle cx="30.55" cy="28.2" r=".4" fill="#262626" /><circle cx="32.45" cy="28.2" r=".4" fill="#262626" />
    <path d="M30.6 29.3h1.8l-.9 1.1z" fill="#F7A531" />
    <path d="M29.2 30.5h4.6v1.1h-4.6z" fill="#E5413B" />
  </svg>
  <!-- 163邮箱 -->
  <svg v-else-if="brand === '163'" :width="size" :height="size" viewBox="0 0 40 40" aria-hidden="true" focusable="false">
    <rect x="1" y="1" width="38" height="38" rx="9.5" fill="#DD4037" />
    <rect x="11.5" y="8" width="17" height="11.5" rx="1.8" fill="none" stroke="#fff" stroke-width="1.6" />
    <path d="M12.4 9.2 20 14.4l7.6-5.2" fill="none" stroke="#fff" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" />
    <text x="20" y="31.2" text-anchor="middle" font-family="Arial, Helvetica, sans-serif" font-size="12.5" font-weight="700" fill="#fff" letter-spacing="-.2">163</text>
  </svg>
  <!-- Outlook（预留） -->
  <svg v-else-if="brand === 'outlook'" :width="size" :height="size" viewBox="0 0 40 40" aria-hidden="true" focusable="false">
    <rect x="1" y="1" width="38" height="38" rx="9.5" fill="#0F6CBD" />
    <text x="19" y="27.8" text-anchor="middle" font-family="Arial, Helvetica, sans-serif" font-size="20" font-weight="700" fill="#fff">O</text>
    <rect x="26.5" y="24.5" width="10" height="7.5" rx="1.4" fill="#fff" />
    <path d="M27.2 25.4 31.5 28.8 35.8 25.4" fill="none" stroke="#0F6CBD" stroke-width="1.1" stroke-linecap="round" stroke-linejoin="round" />
  </svg>
  <!-- iCloud 邮箱（预留） -->
  <svg v-else-if="brand === 'icloud'" :width="size" :height="size" viewBox="0 0 40 40" aria-hidden="true" focusable="false">
    <rect x="1" y="1" width="38" height="38" rx="9.5" fill="#5AA2F0" />
    <rect x="6.5" y="11" width="24" height="17" rx="2.6" fill="#fff" />
    <path d="M8.1 13.2 18.5 20.6 28.9 13.2" fill="none" stroke="#5AA2F0" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" />
  </svg>
  <!-- 腾讯企业邮箱：信封 + 企业角标 -->
  <svg v-else-if="brand === 'exmail'" :width="size" :height="size" viewBox="0 0 40 40" aria-hidden="true" focusable="false">
    <rect x="1" y="1" width="38" height="38" rx="9.5" fill="#2E6BE6" />
    <rect x="6.5" y="11" width="24" height="17" rx="2.6" fill="#fff" />
    <path d="M8.1 13.2 18.5 20.6 28.9 13.2" fill="none" stroke="#2E6BE6" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" />
    <circle cx="31.5" cy="29.5" r="7" fill="#fff" />
    <path d="M29 32.4V26.6h5v5.8M27.8 32.4h7.4" fill="none" stroke="#2E6BE6" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round" />
    <path d="M30.5 28.4h.01M32.5 28.4h.01M30.5 30.3h.01M32.5 30.3h.01" stroke="#2E6BE6" stroke-width="1.4" stroke-linecap="round" />
  </svg>
  <!-- 网易企业邮箱：信封 + 企业角标 -->
  <svg v-else-if="brand === 'qiye'" :width="size" :height="size" viewBox="0 0 40 40" aria-hidden="true" focusable="false">
    <rect x="1" y="1" width="38" height="38" rx="9.5" fill="#A62B25" />
    <rect x="6.5" y="11" width="24" height="17" rx="2.6" fill="#fff" />
    <path d="M8.1 13.2 18.5 20.6 28.9 13.2" fill="none" stroke="#A62B25" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" />
    <circle cx="31.5" cy="29.5" r="7" fill="#fff" />
    <path d="M29 32.4V26.6h5v5.8M27.8 32.4h7.4" fill="none" stroke="#A62B25" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round" />
    <path d="M30.5 28.4h.01M32.5 28.4h.01M30.5 30.3h.01M32.5 30.3h.01" stroke="#A62B25" stroke-width="1.4" stroke-linecap="round" />
  </svg>
  <!-- 自定义邮箱 -->
  <svg v-else :width="size" :height="size" viewBox="0 0 40 40" aria-hidden="true" focusable="false">
    <rect x="1" y="1" width="38" height="38" rx="9.5" fill="#F3F4F6" stroke="#E5E7EB" />
    <rect x="8" y="10.5" width="19" height="14" rx="2.4" fill="#fff" stroke="#4B5563" stroke-width="1.7" />
    <path d="M9 12.2 17.5 18.4 26 12.2" fill="none" stroke="#4B5563" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" />
    <circle cx="28.5" cy="28.5" r="7" fill="#fff" stroke="#D1D5DB" />
    <path d="M28.5 25.3v6.4M25.3 28.5h6.4" stroke="#4B5563" stroke-width="1.7" stroke-linecap="round" />
  </svg>
</template>

<script setup lang="ts">
import type { MailboxBrandKey } from './mailboxBrands'

/**
 * 连接邮箱品牌图标。
 * SVG 形状照搬交互设计稿 docs/prototypes/connect-mailbox/app.js 的 brandIcon()，
 * 品牌色为设计稿示意色（允许硬编码，不受语义 token 约束）。
 */
withDefaults(defineProps<{
  brand: MailboxBrandKey
  size?: number
}>(), {
  size: 40,
})
</script>
