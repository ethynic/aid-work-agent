/** 仅提示用户选择执行模式；文本匹配本身不授予持续执行。 */
export function suggestsContinuousMode(text: string): boolean {
  return /(请|帮我|你能|现在开始|从现在开始|开始)/.test(text)
    && /(监控|监听|跟进|巡查|检查|回复|处理|执行)/.test(text)
    && /(持续|一直|长期|每隔.{0,8}(秒|分钟|小时)|直到.{0,12}(停止|停下|取消))/.test(text)
    && !/(如何|怎么|怎样|方案|方法|教程|设计|开发|实现|不要持续|取消持续|停止持续)/.test(text)
}
