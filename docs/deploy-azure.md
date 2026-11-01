# 部署到 Azure App Service（Linux）

原則：**不新增成本**。程式本身以環境變數設定，B1 與 F1 用同一份程式，改方案只需改 App Service Plan 的 SKU。

## 已知取捨（請先讀）
- 目前設定為**不需登入**（使用者決定）：任何知道網址的人都能上傳與下載文件。緩解：網址用隨機名稱、`robots.txt`／`X-Robots-Tag` 禁止索引、暫存檔 30 分鐘自動刪除、同時處理數與檔案大小有上限。**不是真正的存取控制。**
- 之後想加密碼：只要在 App Service 設定 `WMR_PASSWORD`（不需改程式、不需重新部署）。
- 文件會經過 Azure 主機（暫存在 `/tmp`，到期刪除）。若學校或研究規範不允許文件上雲，請不要部署。

## 為了日後能無痛降到 F1
- 不依賴 Always On（F1 不支援）：程式啟動快，不在記憶體保存狀態；冷啟動後舊工作會「已過期」，頁面提示重新掛號即可。
- 資源保守：預設單檔 20MB、同時最多 2 件；只用 `python-docx`、`lxml`。
- 以上數值皆可用 App Settings 調整。F1 的限制（每日 CPU 分鐘數、記憶體）請以 Azure 官方頁面為準。

## 實際部署紀錄（2026-10-31）
- 命令列 `az login` 被租用戶的 security defaults 擋住（AADSTS530035），改在入口網站的 Cloud Shell（暫時性工作階段，不建立儲存體帳戶）執行 `deploy/azure_deploy.sh`。
- 網址：`https://wmr-9b0686fe.azurewebsites.net/`（資源群組 `wmr-rg`，區域 eastasia，B1）。
- 驗證：首頁 200、`robots.txt`、`X-Robots-Tag`、非允許 Host 被拒；用只有一句話的測試檔跑完 upload→clean→review→final→download，複掃零發現。**未用真實論文測試**。

## GitHub Actions 自動部署
推到 `main` 後，`.github/workflows/deploy-azure.yml` 會先跑測試，通過才部署，最後檢查網站有回應。只改 `docs/`、`*.md`、`bench/` 不會觸發；也可在 Actions 頁面手動執行。

只需設定一次（依 Microsoft Learn 與 GitHub 文件，2026-11 核對）：

**Azure 入口網站**（App Services → `wmr-9b0686fe`）
1. 「Configuration → General settings」：**SCM Basic Auth Publishing Credentials** 設為 On，儲存。
2. 「Environment variables」：新增 `WEBSITE_WEBDEPLOY_USE_SCM`＝`true`（Linux 必須在下載設定檔**之前**設定），儲存。
3. 回到 Overview，按 **Download publish profile**，下載 `.PublishSettings` 檔。

**GitHub**（不是 Azure）：`https://github.com/hsiliang/Watermark_remover/settings/secrets/actions`
4. Secrets 分頁 → New repository secret；Name 填 `AZURE_WEBAPP_PUBLISH_PROFILE`，Secret 貼上該檔的全部內容。

發行設定檔等同部署密碼，不要貼到 issue 或聊天室；外洩時在入口網站重設。

## 手動重新部署（Cloud Shell，備用）
1. 本機：`git archive --format=zip HEAD -o out/wmr.zip`，在 Cloud Shell 上傳覆蓋 `~/wmr.zip`。
2. `az webapp deploy -g wmr-rg -n wmr-9b0686fe --src-path ~/wmr.zip --type zip`

## 步驟（命令列可用時）
```bash
az login                                   # 瀏覽器登入（學生帳號）
az account show                            # 確認訂閱名稱與狀態
RG=wmr-rg; REGION=eastasia; APP=wmr-$(openssl rand -hex 4)
az group create -n $RG -l $REGION
az appservice plan create -g $RG -n wmr-plan --is-linux --sku B1
az webapp create -g $RG -p wmr-plan -n $APP --runtime "PYTHON:3.11"
az webapp config appsettings set -g $RG -n $APP --settings \
  SCM_DO_BUILD_DURING_DEPLOYMENT=true WEBSITES_PORT=8000 \
  WMR_ALLOWED_HOSTS=$APP.azurewebsites.net WMR_MAX_MB=20 WMR_TTL_MIN=30 WMR_MAX_CONCURRENT=2 WMR_DATA_DIR=/tmp/wmr
az webapp config set -g $RG -n $APP --startup-file "python -m wmr web --host 0.0.0.0 --port 8000"
az webapp config set -g $RG -n $APP --https-only true 2>/dev/null || az webapp update -g $RG -n $APP --https-only true
git archive --format=zip HEAD -o /tmp/wmr.zip
az webapp deploy -g $RG -n $APP --src-path /tmp/wmr.zip --type zip
echo https://$APP.azurewebsites.net/
```

## 降到免費層 F1（B1 額度用完前）
```bash
az appservice plan update -g wmr-rg -n wmr-plan --sku F1
```
若 F1 在你的訂閱／區域不可用，直接刪除資源群組即可停止計費：`az group delete -n wmr-rg --yes`。

## 啟用密碼（可隨時）
```bash
az webapp config appsettings set -g wmr-rg -n $APP --settings WMR_PASSWORD='你的密碼'
```

## 檢查成本
Azure 入口網站 → Cost Management。學生訂閱的剩餘額度與到期後行為，請以入口網站顯示為準。
