"""微信会话任务场景（weixin.conversation.v1，C1 骨架）。

场景适配器（compile_operations/authorize 等）在 C3 接底座执行时注册；C1 只
交付会话绑定骨架（pending → verified 仅接受受信 Provider 真机证据）与场景常量。
"""
