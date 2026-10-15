# tests/py/gen_vectors.py — 独立来源固定测试向量生成器（任务 1.5）
#
# 用 ref_encryptor（sqlite3mc wxaes128 规格独立誊写的加密侧）生成固定向量并写入
# fixtures/wxaes128-vectors.json（生成器与产物都入库；测试只消费入库产物，不重新生成）。
#
# 向量集（mkey 均为测试专用合成密钥，非真实密钥）：
#   page1-relocated-header / normal-page-2/3/4：同一 mkey、页号 1-4 连续，
#       明文取自真实 SQLite 生成的恰好 4 页最小库（整库即向量集，供测试把密文拼回原库
#       走 decrypt 全链路并过 integrity_check——截断的真库会因页 1 头 dbsize 与文件页数
#       不符判 malformed，故底座按页数精调而非截取；单表根叶首次分裂恒 +2 页，2→4 可
#       精确命中，2→3 不可达）。
#   zero-payload-page-7：全零明文页（空满载荷页，仅 decrypt_page 单元对照）；
#   page1-second-key：同页 1 明文、另一 mkey（证密文随密钥变化，防向量与钥脱钩）。
#
# 运行： venv python tests/py/gen_vectors.py（需 pycryptodome）
# 说明：明文库由本机 SQLite 生成，跨版本重跑可能产出不同（明文, 密文）对——两者同源变化
# 不影响测试有效性（断言是「驱动解密向量密文 == 向量明文」）；重跑后按 diff 审查入库即可。
import json
import os
import sqlite3
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import ref_encryptor  # noqa: E402

VECTORS_PATH = os.path.join(_HERE, 'fixtures', 'wxaes128-vectors.json')

# 测试专用合成密钥（固定常量；与真实密钥无关）
MKEY_A = bytes(range(16)).hex()                    # 000102...0f
MKEY_B = bytes(reversed(range(16))).hex()          # 0f0e...00（另一独立钥）


def build_plain_db(target_pages=4):
    """恰好 target_pages 页的真实 SQLite 库（逐行插入至文件页数达标，整库即向量明文集）。

    页数精调依据：单表（无自增序列表）从 2 页（头 + 根叶）起步，根叶首次分裂恒 +2 页
    （根转内结点 + 两新叶），故 4 页可精确命中、3 页不可达。"""
    tmp = tempfile.TemporaryDirectory(prefix='wxaes128-vectors-')
    try:
        path = os.path.join(tmp.name, 'plain.db')
        conn = sqlite3.connect(path)
        try:
            conn.execute('PRAGMA page_size=4096')
            conn.execute('CREATE TABLE t(id INTEGER PRIMARY KEY, txt TEXT)')
            conn.commit()
            i = 0
            while os.path.getsize(path) < target_pages * ref_encryptor.PAGE_SIZE:
                conn.execute('INSERT INTO t(txt) VALUES (?)',
                             ('独立向量底座-%03d-' % i + 'x' * 400,))
                conn.commit()
                i += 1
        finally:
            conn.close()
        with open(path, 'rb') as f:
            raw = f.read()
    finally:
        tmp.cleanup()
    pages = len(raw) // ref_encryptor.PAGE_SIZE
    assert pages == target_pages, \
        '底座库应恰好 %d 页，实际 %d（行宽需调，防止跨页跳变越标）' % (target_pages, pages)
    return raw


def main():
    raw = build_plain_db()
    page = lambda pgno: raw[(pgno - 1) * ref_encryptor.PAGE_SIZE: pgno * ref_encryptor.PAGE_SIZE]

    def vector(name, mkey_hex, pgno, plain):
        mkey = bytes.fromhex(mkey_hex)
        return {
            'name': name,
            'mkey_hex': mkey_hex,
            'pgno': pgno,
            'plaintext_hex': plain.hex(),
            'ciphertext_hex': ref_encryptor.ref_encrypt_page(mkey, pgno, plain).hex(),
        }

    vectors = [
        vector('page1-relocated-header', MKEY_A, 1, page(1)),
        vector('normal-page-2', MKEY_A, 2, page(2)),
        vector('normal-page-3', MKEY_A, 3, page(3)),
        vector('normal-page-4', MKEY_A, 4, page(4)),
        vector('zero-payload-page-7', MKEY_A, 7, bytes(ref_encryptor.PAGE_SIZE)),
        vector('page1-second-key', MKEY_B, 1, page(1)),
    ]
    fixture = {
        'schema_version': 1,
        'source': 'independent reference implementation (tests/py/ref_encryptor.py, sqlite3mc wxaes128 spec)',
        'generator': 'tests/py/gen_vectors.py',
        'note': '测试专用合成密钥；页 1 为非遗留搬移布局；page1/2/3 同 mkey 连续页号供 decrypt 全链路拼库',
        'vectors': vectors,
    }
    os.makedirs(os.path.dirname(VECTORS_PATH), exist_ok=True)
    with open(VECTORS_PATH, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(fixture, f, ensure_ascii=True, indent=1)
        f.write('\n')
    print('written: %s (%d vectors)' % (os.path.relpath(VECTORS_PATH, _HERE), len(vectors)))


if __name__ == '__main__':
    main()
