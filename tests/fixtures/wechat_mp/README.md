# wechat_mp WP3 测试夹具

| 文件 | 来源 | 用途 |
|------|------|------|
| `article_masssend_shortlink.html` | 真实抓取（2026-09-14）：`http://mp.weixin.qq.com/s/h8NxFr8whGfZ-KjO_AQZUA`（WP0 群发样本，公开营销文章） | 正文提取（标题/js_content 内 17 图/保序序列）、别名 msg_link=短链形态 |
| `article_freepublish.html` | 真实抓取（2026-09-14）：getarticle 返回的长链 URL（发布渠道样本，同主题另一篇） | 正文提取、别名 msg_link=长链形态（含 chksm、`&amp;` 转义） |
| `article_freepublish.url.txt` | getarticle 返回的原始长链（公开文章 URL，非凭据） | identity 长链规范化用例输入 |
| `verify_page_real.html` | 真实抓取（2026-09-14）：同一短链首次请求被风控返回的验证页（`PAGE_MID='mmbizwap:secitptpage/verify.html'`） | risk_blocked 判定（验证页绝不误判删除） |
| `deleted_page.html` | **手工合成**（模拟 weui-msg 错误页结构 + 删除文案；真实删除页待有样本后回填） | deleted 多信号判定 |
| `unrecognized_page.html` | 手工合成（200 但无 js_content/无错误页 DOM/无验证信号） | fetch_failed 判定（不误判删除） |
| `article_quoting_deleted_phrase.html` | 手工合成（js_content 内引用删除文案的正常文章） | 防误删负样本：判 ok |

凭据（appid/secret/access_token）不进入本目录；真实页面为公开营销文章内容，可入库。
