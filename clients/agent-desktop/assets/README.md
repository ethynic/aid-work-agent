# Runtime 品牌图标

`runtime.ico` 复用官网 <https://www.aidingyi.cn/favicon.ico>（原始来源与 `frontend/public/favicon.ico` 一致），经用户于 2026-11-02 确认采用，不另造品牌图形。

官网原文件含 16、32、48 像素尺寸；保留这三份原帧字节，并用Windows标准图像编码补充256像素PNG帧，以满足electron-builder的ICO尺寸要求，不生成新图形。SHA256见构建输入清单。Runtime EXE、NSIS 安装/卸载图标使用同一资产；窗口和界面复用 renderer 内的同源 favicon。高 DPI 下原图资源清晰度受官网原始尺寸限制，后续取得官方高分辨率图标时替换同一资产。
