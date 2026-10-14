# BOSS 头像下方跨 iframe VIP 徽标识别

## 开发进度

| 阶段 | 内容 | 状态 | 完成记录 |
|------|------|------|---------|
| Phase 1 | 用户标注与真机结构确认 | ✅ 完成（2026-10-13） | 主页面avatar-content，已确认VIP徽标实际在推荐iframe的IMG.vip-icon-v2，旧代码返回unknown |
| Phase 2 | 修正会员观察规则 | ✅ 完成（2026-10-13） | Provider0.3.5；按头像相对位置、元素/iframe裁剪识别；账号key与负证据规则不变 |
| Phase 3 | 独立验证、真机只读验收与提交 | ✅ 完成（2026-10-13） | 独立40项及额外8场景通过，CR无阻断；真机连续3次vip/source avatar；用户授权提交推送，未部署 |

## 实测与修正

用户提供标注截图并确认头像下方橙色VIP标用于判断账号会员。CLI Gateway只读采集确认：主页面头像device bounds为[2908,11.78125,60,60]；推荐iframe的IMG.vip-icon-v2跨frame映射后为[2910,82,66,32]。图像地址为HTTPS img.bosszhipin.com/static/zhipin/recommend-geek/955336197567184833.png，祖先链filter-label-wrap→filter-wrap→recommend-filter→head-wrap。旧实现只检查头像DOM内部并一律排除该图标，因此漏判为unknown；此前把所有vip-icon-v2当推广的结论现按用户补充纠正。

识别必须绑定唯一可见导航头像，徽标图片/结构正确且在其正下方，跨iframe累加坐标并检查iframe可见性/裁剪；不能仅因页面任意位置出现VIP、类名或图片就识别。使用头像相对比例适应DPI/窗口，不写死当前绝对坐标。缺少、隐藏、错位、重复或不可确认的徽标仍unknown，不把缺少徽标推断为非VIP。明确头像non_vip与付费弹窗负证据仍优先；账号摘要/跨调用提示绑定不变。

已读共同集成契约v2.0；仅Provider会员观察修复，无Runtime core、Device wire、授权或数据库变更。修复版本0.3.4→0.3.5，输入/输出schema不变。范围明确的局部bug按常规流程：主控开发+定向自测，一个独立智能体测试并CR。独立验证不操作真机，主控仅只读采集，不修改筛选或点击高级项。

## 验证与交付

覆盖跨iframe徽标、缩放、滚动、隐藏祖先/owner、无owner/裁剪、错误位置/图片/结构、重复证据、非VIP优先、账号绑定、付费回退及旧头像内部标识。真实非VIP账号仍未具备，负例使用结构变体，不能声称非VIP真机通过。更新原VIP计划与完成索引，保留历史验证时间事实；仅本任务改动提交并推送已确认upstream，不部署。

## 验证记录

- 主控build与39项裁剪增量前的定向测试退出0，最终独立build与40项测试退出0，范围avatar-vip-marker/filter-entitlements/filter-vip-panel/filter-page-gate；账号绑定及付费回退原回归继续通过。
- 真机通过CLI Gateway fresh canonical DOMSnapshot连续采集3次，inspectMembership均为vip/source avatar，账号摘要存在且保持一致；每次将本地克隆快照中的徽标结构移除后为unknown，原快照仍vip，未修改真实页面。只读0输入，探针为毫秒级；未持久化原始账号信息。主控提交前对最终源码重新build及真机只读复验通过。
- 初始验证子智能体3次因模型容量不足未启动，不计为验证通过。用户明确允许gpt-6.1-sol后独立验证通过：40项定向回归、额外8个内存反例（多owner、嵌套双层滚动、iframe裁剪、隐藏owner、owner祖先overflow、owner局部可见、sparse、账号摘要不变）全部通过，CR无P0/P1/P2问题。
- 本任务按用户授权提交并推送已确认的origin/master，不部署；真实非VIP视觉/完全遮挡场景仍未验收，保留原付费阻断与未知澄清作为兜底。
