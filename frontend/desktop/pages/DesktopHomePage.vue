<template>
  <section class="workspace-page" aria-labelledby="workspace-title">
    <div class="page-kicker">DESKTOP / INTERNAL ALPHA</div>
    <div class="home-heading">
      <div>
        <h1 id="workspace-title">执行工作台</h1>
        <p>桌面端安全边界与身份已就绪。Agent 工作区将在 D2 接入。</p>
      </div>
      <span class="phase-badge">Phase C</span>
    </div>
    <div class="readiness-grid">
      <article><span class="metric-index">01</span><h2>独立渲染器</h2><p>Desktop UI 与 Web 页面、路由及全局样式完全隔离。</p><span class="readiness-state ready">已就绪</span></article>
      <article><span class="metric-index">02</span><h2>安全身份</h2><p>会话凭证仅通过系统安全存储持久化，不写入 localStorage。</p><span class="readiness-state ready">已保护</span></article>
      <article><span class="metric-index">03</span><h2>Agent 工作区</h2><p>对话、会话与工具调用由后续 Coordinator 阶段接管。</p><span class="readiness-state pending">D2 待接入</span></article>
    </div>
    <aside class="scope-note">
      <span class="scope-rule" aria-hidden="true" />
      <div><strong>当前发布范围</strong><p>本版本用于验证独立 Shell、登录、离线提示与更新阻断，不提供真实 Agent 对话。</p></div>
    </aside>
  </section>
</template>
