// 根据访问域名判定部署环境，复制错误/异常详情时自动注明，免去手动输入
export function resolveEnvLabel(): string {
  const host = window.location.hostname
  if (host === 'agent.aidingyi.cn') return 'agent生产环境'
  if (host === 'agent1.aidingyi.cn') return 'agent1仿真环境'
  if (host === 'agent2.aidingyi.cn') return 'agent2测试环境'
  return host + '环境'
}
