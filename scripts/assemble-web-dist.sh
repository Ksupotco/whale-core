#!/usr/bin/env bash
# 组装 whale-api 静态托管面 web/dist：/ = 新控制台，/classic/ = 高级台。
# 用法：bash scripts/assemble-web-dist.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/web"
echo "[1/3] classic 高级台构建（base /classic/）"
pnpm exec vite build --base /classic/ --outDir dist-classic --emptyOutDir
echo "[2/3] 新控制台构建（独立同源 JS/CSS，过 CSP）"
cd "$ROOT/web/console"
node scripts/build-for-whale.mjs
echo "[3/3] 组装 web/dist"
cd "$ROOT/web"
rm -rf dist
mkdir -p dist/classic
cp -r console/dist-whale/. dist/
cp -r dist-classic/. dist/classic/
rm -rf dist-classic
echo "web/dist 组装完成：/=新控制台，/classic/=classic 高级台"
