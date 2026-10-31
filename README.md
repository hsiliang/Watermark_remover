# Watermark_remover

檢測並消除 AI（Claude 等）生成之 Word（.docx）檔中的隱藏痕跡。**個人自用，不公開。**

用途背景：與同學共同研究「AI 生成 Word 檔的隱藏痕跡偵測」，本工具負責「消除」這一側，用來檢驗偵測系統的有效性。

> **核心原則**：AI 只參與「想怎麼改」；真正產出最終檔案的步驟（重建、寫回、改作者／公司屬性）全部是**純本地、確定性的 Python 腳本**，不呼叫任何 AI。

---

## 目錄
1. [它能做什麼](#它能做什麼)
2. [安裝](#安裝)
3. [快速開始](#快速開始)
4. [網頁介面](#網頁介面)
5. [命令列用法](#命令列用法)
6. [建議工作流程](#建議工作流程)
7. [痕跡分哪幾層](#痕跡分哪幾層)
8. [驗證與已知限制](#驗證與已知限制)
9. [專案結構](#專案結構)
10. [常見問題](#常見問題)

---

## 它能做什麼

| 功能 | 說明 | 是否用 AI |
|---|---|---|
| **檔案層檢測** | 掃描 zip 結構、套件部件、文件屬性、XML 殘留、隱藏文字、rsid／paraId 等痕跡 | 否 |
| **白名單重建** | 只保留文件必要部件，丟棄 customXml、縮圖、舊 app／core 屬性，剝除 rsid、proofErr、書籤殘留、圖片 metadata，並重排成 Word 風格的 zip | 否 |
| **屬性改寫** | 作者／最後修改者＝`梁曦414155039`，公司＝`天主教輔仁大學`（最後一步，純本地） | 否 |
| **文字風格層**（選用） | 規則／詞表式：殘留標記、套話刪除、慣用語替換、兩岸用語、Markdown 殘留、emoji、破折號、標點 | 否 |
| **可疑部位報告** | 統計各類 AI 文風訊號（句長均勻、否定排比、模糊引用…）並標出位置，供**人工**判斷與修改 | 否 |
| **匯出／寫回改寫** | 把可疑段落匯出成 JSON，交給 AI 或自己改寫，再**純本地**寫回，保留格式 | 改寫由你決定；寫回否 |
| **格式保真驗證** | 逐字元比對格式，並可用 LibreOffice 渲染逐頁比對畫面 | 否 |
| **網頁介面** | 本機瀏覽器操作：上傳 → 檢測 → 清理 → 下載 | 否 |

---

## 安裝

需要：macOS／Linux、Python 3.9 以上。

```bash
cd Watermark_remover
python3 -m venv .venv && source .venv/bin/activate     # 建議使用虛擬環境
pip install -r requirements.txt
```

選用的外部軟體：

| 軟體 | 用在哪 | 沒有的話 |
|---|---|---|
| LibreOffice | `--render-check` 逐頁畫面比對 | 其他功能不受影響 |
| Word for Mac | `--word`、`--page-check`（選用） | 其他功能不受影響 |

驗證安裝：

```bash
python3 -m pytest -q          # 應全部通過
```

---

## 快速開始

最常用的一行（清理 + 風格層 + 報告 + 畫面比對）：

```bash
python3 -m wmr clean 論文.docx -o out/乾淨.docx --style --review --render-check
```

這會依序：掃描原檔 → 白名單重建 → 改作者／公司 → 複掃 → 保真比對 → 產生可疑部位報告與標註副本。
輸出在 `out/` 資料夾。**請用 `out/乾淨.docx` 繳交；`*_標註.docx` 只供檢閱，不要繳交。**

---

## 網頁介面

```bash
python3 -m wmr web                 # 預設埠 8765
python3 -m wmr web --port 9000     # 指定埠
```

看到「本機網頁介面：http://127.0.0.1:8765/」後，**保持該終端機視窗開著**（按 Ctrl+C 即結束），用瀏覽器開啟網址。

操作步驟：
1. 拖曳 .docx 到頁面（或點選選擇檔案）。
2. 頁面顯示**檔案層檢測**表與**可疑部位統計**；點「開啟完整報告」可看位置與可複製的 Word 搜尋字串。
3. 視需要勾選：
   - 「啟用文字風格層」：規則式替換（不處理參考文獻）。
   - 「保留隱藏文字」：預設會刪除隱藏文字。
4. 按「清理並產生新檔」。
5. 看複掃結果、保真比對、風格層變更；按「下載清理後的 .docx」。

注意：
- 只綁 `127.0.0.1`，並拒絕 Host 不是 localhost／127.0.0.1 的請求；**無登入機制，請勿改綁其他位址**。
- 網頁不含：Word 重存、LibreOffice 渲染比對、匯出／寫回改寫。這些請用命令列。
- 暫存檔放在 `out/web/<id>/`（含你的文件），**每次啟動會清空**；`--keep` 可保留。

---

## 命令列用法

所有指令都是 `python3 -m wmr <子指令>`。

### `scan` — 只檢測，不修改
```bash
python3 -m wmr scan 檔案.docx            # 檔案層痕跡
python3 -m wmr scan 檔案.docx --style    # 一併檢測文字風格痕跡
python3 -m wmr scan 檔案.docx --json     # JSON 輸出
```
發現高嚴重度項目時結束碼為 1。

### `clean` — 清理（主要指令）
```bash
python3 -m wmr clean 原檔.docx -o out/乾淨.docx [選項]
```

| 選項 | 說明 |
|---|---|
| `--style` | 啟用文字風格層（規則式，選用）；會印出變更日誌 |
| `--style-rules 群組,群組` | 指定風格群組，可用：`artefact, filler, lexicon, region, markdown, emoji, dash, punct, quotes`（`quotes` 預設關） |
| `--review` | 清理後產生可疑部位 HTML 報告與標註副本 |
| `--render-check` | 用 LibreOffice 渲染原檔與輸出，逐頁比對畫面（格式保真的最直接證據） |
| `--keep-hidden` | 不刪除隱藏文字 |
| `--word` | 再用 Word for Mac 重存一次（**選用，不建議預設使用**，見下方警告） |
| `--page-check` | 用 Word 重存原檔與輸出，比對 Word 計算的頁數／字數 |

> **關於 `--word`**：Word 重存會改寫樣式定義。在真實論文上實測，LibreOffice 渲染結果與原檔不同（58 頁對 56 頁），所以預設不用；若要用，請務必搭配 `--render-check`。
> 本機 Word 的「另存新檔」不可用，改採「開啟 → 標記已修改 → 關閉並儲存」。這會彈出 Word 視窗，可能需要你在場確認。

**風格層的套話刪除規則**：「綜上所述」「總而言之」「總的來說」「值得注意的是」等，只在**句首或逗號後、後面接逗號、且不在引號內**時才刪除，否則不動，避免留下殘句或改到引文。「綜上所述——」這種破折號寫法不會觸發。

### `review` — 可疑部位報告
有些訊號像 AI，但**不確定、也不該由 AI 自動改寫**（句長均勻、否定排比、模糊引用、連接詞密集…）。這些只統計並標出位置，交給你判斷。

```bash
python3 -m wmr review 檔案.docx                                  # 產生 HTML 報告
python3 -m wmr review 檔案.docx --annotate out/檔案_標註.docx    # 另產生有色標註副本
python3 -m wmr review 檔案.docx --json out/report.json           # 另存 JSON
python3 -m wmr review 檔案.docx --skip-heading 附錄               # 再略過「附錄」章節（可重複）
```

- **HTML 報告**：各類型數量（含信心等級、可否自動修）、熱點段落、章節＋第幾段、**可點擊複製的 Word 搜尋字串**（貼進 Word 的「尋找」即可定位）。
- **標註副本**：在可疑處加螢光筆，並自動避開文件原本就有的螢光筆顏色。**僅供檢閱，勿繳交。**
- 自動略過「參考文獻／參考資料／References／Bibliography」章節。
- 文件層級指標包含句長變異、連接詞開頭比例、「的」字密度、逗號密度、括號密度、破折號數、「具體資訊稀少的段落比例」等。括號密度只供參考，**未設門檻**（沒有資料可判斷多少算偏低）。

### `export` / `apply` — AI／人工改寫，純本地寫回
真正可能影響統計性檢測的是**內容層改寫**（句型、節奏、論述），這件事腳本做不到，需要人或 AI。工具負責「把要改的段落交出去」與「把改好的結果寫回來」。

```bash
# 1. 匯出有可疑訊號的段落（附各段問題與改寫指引）
python3 -m wmr export 稿.docx -o edits.json [--min-conf 低|中|高]

# 2. 把 edits.json 交給 Claude 或自己改：在每項的 new_text 填入改寫，不改的保持 null

# 3. 純本地寫回，保留段落與字元格式
python3 -m wmr apply 稿.docx edits.json -o 稿_改.docx

# 4. 最後的重建＋改屬性
python3 -m wmr clean 稿_改.docx -o out/最終.docx --style --review
```

- `--min-conf 中`／`高`：只匯出中／高信心的段落（實驗中，優先改這些最划算）。
- 含圖片、欄位（目錄／交互參照）、註腳、超連結、公式的段落標為 `editable: false`，請在 Word 手動修改。
- 匯出後若原稿被改動，該段會因「原文不符」被略過，不會誤改。
- `new_text` 可用 `**粗體**` 標記。
- `apply` 加 `--force` 可強制替換含超連結／欄位的段落（可能破壞該段結構，慎用）。
- 改寫時不要新增原文沒有的事實、數據或來源；「模糊引用」的問題 AI 無法憑空補出處，請人工處理。

### `web` — 網頁介面
見 [網頁介面](#網頁介面)。

---

## 建議工作流程

**情境 A：只想清掉檔案層痕跡（格式零風險）**
```bash
python3 -m wmr clean 原檔.docx -o out/乾淨.docx --render-check
```
確認輸出為「渲染比對：一致」、複掃「未發現痕跡」，再用 Word 打開目視一次。

**情境 B：連文字風格也處理，並找出要人工修改的地方**
```bash
python3 -m wmr clean 原檔.docx -o out/乾淨.docx --style --review
```
打開 `out/乾淨_review.html`，依「信心：高／中」的類型優先處理，用報告上的搜尋字串在 Word 裡定位並修改。

**情境 C：大量改寫（AI 輔助）**
1. `export`（建議 `--min-conf 中`）→ 交給 AI 或自己改寫 → `apply`
2. 對 `apply` 的輸出再跑 `clean ... --style --review`（確保最後一步是純本地腳本、屬性正確）
3. 用 `review` 檢查剩餘的可疑部位，人工補強

**最後務必**：
- 用 Word 開啟成品，目視確認版面；
- 到「檔案 → 資訊」確認作者與公司；
- 繳交的是 `clean` 的輸出，不是 `_標註.docx`。

---

## 痕跡分哪幾層

| 層 | 例子 | 處理方式 |
|---|---|---|
| zip／套件 | docx-js 等產生器的 zip 順序、目錄項、時間戳、單引號 XML 宣告、空 rels、雜湊檔名圖片 | 重建時改成 Word 風格 |
| 文件屬性 | 作者、最後修改者、公司、應用程式名稱、縮圖 | 重寫為 `梁曦414155039`／`天主教輔仁大學` |
| XML 殘留 | rsid、paraId、proofErr、`_GoBack` 書籤、customXml | 剝除 |
| 內容隱形字元 | 隱藏文字、零寬字元、圖片 EXIF／PNG metadata | 刪除或清除 |
| 文字殘留 | `contentReference[oaicite:…]`、`utm_source=chatgpt.com`、Markdown 符號（`**`、`#`）、emoji | 風格層刪除 |
| 文字風格 | 套話、連接詞密集、否定排比、三項並列、首尾三段式、句長均勻、模糊引用、內容空泛 | 報告標示，**交人工**或匯出改寫 |

證據強度與資料來源見 [`docs/research-notes.md`](docs/research-notes.md)。

---

## 驗證與已知限制

**已驗證（真實 Claude／docx-js 產物，78 頁論文計畫書）**
- 本地重建：LibreOffice 渲染 56／56 頁與原檔逐頁相同；掃描器零發現；Word 能開啟。
- 加上 `--word`：Word 自算頁數／字數與原檔相同（78 頁、10,597 字），但 LibreOffice 渲染變成 58 頁、每頁皆有差異。
- 網頁介面：在真實論文上完整跑過上傳、清理、下載。

**限制（請如實看待）**
- **未跑過任何商業檢測網站**（知網、維普、朱雀、GPTZero 等）：它們需要登入、驗證碼或付費。請用學校指定的平台自行測試，這是最直接的證據。
- 可疑部位的**精確率／召回率沒有量測**（沒有人工標註的人類／AI 語料）。
- 掃描器反映的是我的假設，**不等於同學的偵測系統**。
- 獨立檢測器實驗樣本很小（3 篇生成文章＋1 份真實論文，長度 5,500～8,000 字），且生成與改寫皆同一模型；機械式 `--style` 對統計性檢測分數幾乎沒有影響，AI 輔助改寫有效但不均勻。詳見 [`docs/experiments.md`](docs/experiments.md)。
- 「補具體事實可降低分數」的假說在小規模實驗中**不成立**，工具的建議文字已據此改寫。
- 風格層是規則式，仍可能有漏刪與誤判；刪除套話採保守策略，寧可漏掉也不改壞句子。
- 本機 Word 的 `save as` 不可用，`--word` 改用「開啟 → 標記已修改 → close saving yes」。

---

## 專案結構

```
wmr/
  cli.py        命令列入口（scan／review／export／apply／clean／web）
  web.py        本機網頁介面（標準庫 http.server）
  scan.py       檔案層掃描
  rebuild.py    白名單重建（含風格層掛接）
  finalize.py   純本地最後一步：改作者／公司屬性
  style.py      文字風格層（規則／詞表）
  suspect.py    可疑部位分析（約 24 類）
  report.py     HTML／JSON 報告
  annotate.py   有色標註副本
  edits.py      export／apply
  verify.py     格式逐字元比對
  render.py     LibreOffice 渲染逐頁比對
  wordmac.py    Word for Mac 重存（選用）
  common.py     共用常數與正則
tests/          pytest 測試
bench/          獨立檢測器基準測試（選用，見下）
docs/           研究筆記、實驗紀錄、設計文件
out/            輸出與暫存（已 gitignore）
```

### 獨立檢測器基準測試（`bench/`，選用）
用開源檢測器量測清理成效（**不是**商業系統的預測）：中文 RoBERTa 分類器 ×3、GPT-2 中文困惑度、Binoculars（Qwen2.5）。需額外安裝大型相依（torch、transformers，並下載數 GB 模型）：

```bash
python3 -m venv .venv && .venv/bin/pip install -r bench/requirements-bench.txt opencc-python-reimplemented python-docx lxml
.venv/bin/python bench/run_bench.py --ref <含 hc3_*.jsonl 與 wiki/*.txt 的資料夾> 文章.docx ... --out r.json --dump chunks.json
.venv/bin/python bench/table.py r.json                              # 對照表
.venv/bin/python bench/flag_corr.py chunks.json 名稱=文章.docx      # 旗標類別 vs 檢測器分數
```

這些檢測器在長篇正式文體上鑑別力有限（例如 zhv3 會把人類寫的維基百科判成 AI），**只適合做「同一篇清理前後」的相對比較**。

---

## 常見問題

**瀏覽器打不開網頁？**
確認執行 `python3 -m wmr web` 的終端機視窗仍開著、沒按 Ctrl+C；網址要完整輸入 `http://127.0.0.1:8765/`（不是 https）。埠被占用時改用 `--port`。

**清理後 Word 版面跑掉？**
先不要用 `--word`。用 `--render-check` 確認渲染一致；若不一致，請把檔案與輸出差異頁告訴我。

**風格層把某個詞改錯了？**
用 `--style-rules` 只開需要的群組（例如不要 `lexicon`），並檢查 `clean` 印出的「風格層變更日誌」。

**為什麼不直接讓 AI 輸出最終檔？**
本專案的規則：最終檔必須由非 AI 的本地腳本產生，避免留下生成痕跡。AI 只提供改寫文字，由 `apply` 與 `clean` 寫進文件。
