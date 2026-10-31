# Watermark_remover（自用，不公開）

檢測並消除 Claude 生成之 .docx 中的隱藏 AI 痕跡；用於對偵測系統做盲測。

## 用法
```
pip3 install -r requirements.txt
python3 -m wmr scan 檔案.docx [--style]                    # 只檢測
python3 -m wmr clean 檔案.docx -o out/乾淨.docx --render-check   # 建議用法
```
選項：
- `--render-check`：用 LibreOffice 渲染原檔與輸出，逐頁比對畫面（格式保真的直接證據）
- `--style`：文字風格層（規則／詞表式，無 AI；跨 run 替換；不動參考文獻；會印出變更日誌）。`--style-rules artefact,filler,...` 可選群組，另有 `quotes`（「」化，預設關）
- `--word`：再用 Word for Mac 重存。**選用**：Word 會改寫樣式定義，實測在真實文件上
  LibreOffice 渲染與原檔不同（58 頁 vs 56 頁），請務必搭配 `--render-check`
- `--page-check`：用 Word 重存原檔與輸出，比對 Word 計算的頁數／字數

## 可疑部位報告（`review`）
有些訊號像 AI 但**不確定、也不該由 AI 來改寫**（句長均勻、否定排比、模糊引用、連接詞密集…）。
這些只統計並標位置，交給人判斷：
```
python3 -m wmr review 檔案.docx --annotate out/檔案_標註.docx    # 產生 HTML 報告 + 有色標註副本
python3 -m wmr clean 檔案.docx -o out/乾淨.docx --style --review # 一次完成
```
- HTML 報告：分類統計（信心／可否自動修）、熱點段落、章節＋第幾段＋**可點擊複製的 Word 搜尋字串**
- 標註副本：在可疑處加螢光筆（自動避開文件原本就有的顏色）。**僅供檢閱，勿繳交**
- 自動略過「參考文獻」章節（`--skip-heading` 可再加）
- 證據強度與來源見 `docs/research-notes.md`

最後一步（`finalize`）永遠是純本地腳本：作者／最後修改者＝梁曦414155039，公司＝天主教輔仁大學。

## 流程
scan → rebuild（白名單重建）→ [Word 重存] → finalize（改屬性、ZIP 外觀）→ 複掃 → 保真比對

## 驗證紀錄（真實 Claude/docx-js 產物，78 頁論文計畫書）
- 本地重建：LibreOffice 渲染 56/56 頁與原檔逐頁相同；掃描器零發現；Word 能開啟
- 加上 `--word`：Word 自算頁數／字數與原檔相同（78 頁、10,597 字），但 LibreOffice 渲染 58 頁、每頁皆有差異

## 已知限制
- 可疑部位的精確率／召回率**沒有量測**（沒有已標註的人類／AI 語料）；只在一份真實論文上人工抽查過誤報並修正
- 掃描器反映我的假設，不等於同學的偵測系統
- 此機器 Word 的 `save as` 不可用，重存改用「開啟→標記已修改→close saving yes」
- 待做：網頁介面（目前僅 CLI）
