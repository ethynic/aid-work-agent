"""宏陶商城产品知识库同步（hongtao_shop 租户专用模块）。

设计 docs/system/hongtao-shop/hongtao-shop-kb-design.md（v1.2）：
商品+论坛两接口合并为以产品为中心的知识库（一个产品 = 一条知识 = 一个检索单元），
VL 解析详情图进向量、图/视频链接进 metadata 供智能体发送，论坛实拍按型号关联挂靠。
零私有表：账本/缓存/配置复用平台通用 src/services/content_sync/ 五表（module='hongtao_shop'）；
分类为租户知识库顶级「产品」（查无则建普通分类）；零侵入通用层——只调用通用
函数，P2 挂载点仅 main.py 路由块与 background_runner 调度块；用户在知识库删除
文档的抑制走同步侧自愈判定（doc 行消失 → user_deleted）。
"""
