#!/usr/bin/env bash
# 打包发布 tgz：解决 @aid/win32-native-input 的 file: 依赖在客户机无法解析的问题。
#
# 背景（2026-11-10 交付前洁净房验证发现）：依赖声明 file:../shared/win32-native-input 在
# 客户机 `npm install -g <tgz>` 时按安装机的包目录相对解析（<global>/node_modules/shared/...，
# 不存在）→ 悬空符号链接，koffi 缺失，点击/输入等全部 native 动作在 import 处崩溃；
# version --json 不触 native 所以此前未暴露。
#
# 方案：打包时把 shared 包构建产物同步为包内 vendor/win32-native-input，依赖改写为
# file:./vendor/...（随包内容安装，解析恒成立）；koffi（无自身依赖，1.9MB）一并打入
# vendor 包内 node_modules——npm 对包内 file: 链接依赖不安装其依赖树，必须在场。
#
# 用法：bash scripts/pack-release.sh
# 产出：客户端根目录 boss-resume-assistant-<版本>.tgz
set -euo pipefail
cd "$(dirname "$0")/.."

npm run build:main

stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT
mkdir -p "$stage/vendor"
cp -r ../shared/win32-native-input "$stage/vendor/win32-native-input"
# vendor 只留运行时产物（src/tests/tsconfig 是开发资产，dist/package.json 是运行所需）
rm -rf "$stage/vendor/win32-native-input/src" \
       "$stage/vendor/win32-native-input/tests" \
       "$stage/vendor/win32-native-input/tsconfig.json"
cp -r dist scripts "$stage/"
# koffi 打进 vendor 包内（解析链：vendor/.../bindings/koffi.js → vendor/win32-native-input/node_modules）；
# koffi 原生二进制在其平台子包 @koromix/koffi-win32-x64（optionalDependencies，win32-only 交付只带它）
mkdir -p "$stage/vendor/win32-native-input/node_modules/@koromix"
cp -r ../shared/node_modules/koffi "$stage/vendor/win32-native-input/node_modules/koffi"
cp -r ../shared/node_modules/@koromix/koffi-win32-x64 "$stage/vendor/win32-native-input/node_modules/@koromix/koffi-win32-x64"

node -e '
const fs = require("fs");
const stage = process.argv[1];
const p = JSON.parse(fs.readFileSync("package.json", "utf8"));
p.dependencies["@aid/win32-native-input"] = "file:./vendor/win32-native-input";
p.files = Array.from(new Set([...(p.files ?? []), "vendor"]));
delete p.scripts.prepack;              // stage 内无 ../shared；构建已在上面完成
delete p.scripts["build:native"];
fs.writeFileSync(stage + "/package.json", JSON.stringify(p, null, 2) + "\n");
' "$stage"

npm pack "$stage" --pack-destination .
echo "=== 产出：$(pwd)/boss-resume-assistant-$(node -p 'require("./package.json").version').tgz ==="
