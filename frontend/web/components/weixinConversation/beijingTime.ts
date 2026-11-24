const options: Intl.DateTimeFormatOptions = {
  timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
  hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
}
const beijingTime = new Intl.DateTimeFormat('zh-CN', options)
const beijingTimeSeconds = new Intl.DateTimeFormat('zh-CN', { ...options, second: '2-digit' })

export function formatBeijing(value: string | number, seconds = false) {
  return (seconds ? beijingTimeSeconds : beijingTime).format(new Date(value))
}
