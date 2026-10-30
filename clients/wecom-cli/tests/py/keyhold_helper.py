# tests/py/keyhold_helper.py — keyscan 合成进程测试辅助进程（任务 1.6；不依赖真实企微）
#
# 用法：python keyhold_helper.py <key_hex>
# 行为：把 16B 合成密钥作为 bytes 对象 + 噪声填充长期持有（大 blob 内 16 对齐偏移——
# 大 bytes 走 malloc、Windows 堆返回 16 对齐地址，prefix 长度取 16 的倍数使密钥相对
# blob 数据起点亦对齐，供 aligned 预筛确定性命中）；stdout 打印自身 pid（flush）后
# 阻塞休眠，由父进程 terminate 收尾。
# 密钥为测试专用合成随机值，经 argv 传递仅存在于本测试进程树（不涉及真实凭据）。
import os
import sys
import time


def main():
    key = bytes.fromhex(sys.argv[1])
    prefix = bytes(range(256)) * 32          # 8192B 噪声前缀（长度为 16 的倍数）
    suffix = bytes(range(255, -1, -1)) * 32  # 8192B 噪声后缀
    blob = prefix + key + suffix             # 密钥在 blob 内 16 对齐偏移
    hold = (key, blob, len(blob))            # 多引用防回收（CPython 不优化局部变量，防御性）
    print(os.getpid(), flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
    _ = hold


if __name__ == '__main__':
    main()
