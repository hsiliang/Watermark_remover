#!/usr/bin/env bash
# 在 Azure Cloud Shell（Bash）執行：bash deploy/azure_deploy.sh
# 會先顯示訂閱與預估成本，輸入 yes 才建立資源。
set -euo pipefail
cd "$(dirname "$0")/.."
ZIP="${WMR_ZIP:-$HOME/wmr.zip}"
[ -f "$ZIP" ] || { echo "找不到 $ZIP（請先把 wmr.zip 上傳到 Cloud Shell 的家目錄）"; exit 1; }

echo "== 目前訂閱 =="
az account show --query "{名稱:name, 狀態:state, 訂閱ID:id}" -o table
SKU="${WMR_SKU:-B1}"
RG="${WMR_RG:-wmr-rg}"
APP="${WMR_APP:-wmr-$(openssl rand -hex 4)}"
echo
echo "== 將建立 =="
echo "  資源群組 : $RG"
echo "  方案     : Linux App Service Plan，SKU=$SKU"
echo "  Web App  : $APP  （網址 https://$APP.azurewebsites.net/）"
echo "  區域     : 依序嘗試 eastasia, southeastasia, japaneast, koreacentral, australiaeast, westus2"
echo "  預估成本 : B1 約每月 US\$13（我未核對，請以 Azure 入口網站定價為準）；F1 為免費層"
echo "  登入保護 : 預設不需登入（可之後設定 WMR_PASSWORD）"
read -r -p "確認建立請輸入 yes：" ok
[ "$ok" = "yes" ] || { echo "已取消，沒有建立任何資源。"; exit 0; }

REGION=""
for r in eastasia southeastasia japaneast koreacentral australiaeast westus2; do
  if az group create -n "$RG" -l "$r" -o none 2>/dev/null; then REGION="$r"; break; fi
  echo "區域 $r 不可用，換下一個…"
done
[ -n "$REGION" ] || { echo "所有區域都建立失敗（可能是學生訂閱的區域限制）。"; exit 1; }
echo "資源群組建立於 $REGION"

az appservice plan create -g "$RG" -n wmr-plan --is-linux --sku "$SKU" -l "$REGION" -o none
az webapp create -g "$RG" -p wmr-plan -n "$APP" --runtime "PYTHON:3.11" -o none
az webapp config appsettings set -g "$RG" -n "$APP" -o none --settings \
  SCM_DO_BUILD_DURING_DEPLOYMENT=true WEBSITES_PORT=8000 \
  WMR_ALLOWED_HOSTS="$APP.azurewebsites.net" WMR_MAX_MB=20 WMR_TTL_MIN=30 WMR_MAX_CONCURRENT=2 WMR_DATA_DIR=/tmp/wmr
az webapp config set -g "$RG" -n "$APP" -o none --startup-file "python -m wmr web --host 0.0.0.0 --port 8000"
az webapp update -g "$RG" -n "$APP" --https-only true -o none
echo "部署中（建置約 1–3 分鐘）…"
az webapp deploy -g "$RG" -n "$APP" --src-path "$ZIP" --type zip -o none

URL="https://$APP.azurewebsites.net/"
echo "等待啟動…"
for i in $(seq 1 12); do
  code=$(curl -s -o /dev/null -w '%{http_code}' "$URL" || true)
  [ "$code" = "200" ] && break
  sleep 10
done
echo "HTTP 狀態：$code"
echo "網址：$URL"
echo "停止並刪除全部資源：az group delete -n $RG --yes"
echo "降到免費層：az appservice plan update -g $RG -n wmr-plan --sku F1"
