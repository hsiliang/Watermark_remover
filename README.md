# Watermark_remover（自用，不公開）

檢測並消除 Claude 生成之 .docx 中的隱藏 AI 痕跡；用於對偵測系統做盲測。

## 用法
```
pip3 install -r requirements.txt
python3 -m wmr scan 檔案.docx            # 只檢測
python3 -m wmr clean 檔案.docx -o out/乾淨.docx          # 純本地重建
python3 -m wmr clean 檔案.docx -o out/乾淨.docx --word   # 再用 Word for Mac 重存（建議）
```
加 `--style` 啟用文字風格層（選用，規則／詞表式，無 AI；詞表在 `wmr/style.py` 的 `LEXICON`）。
`scan --style` 可只檢測風格痕跡；`--page-check` 用 Word 重存原檔與輸出，比對頁數／字數。

最後一步（`finalize`）永遠是純本地腳本：作者／最後修改者＝梁曦414155039，公司＝天主教輔仁大學。

## 流程
scan → rebuild（白名單重建）→ [Word for Mac 重存] → finalize（改屬性、ZIP 外觀）→ 複掃 → 保真比對

## 狀態
- 已完成：scan、rebuild、Word for Mac 重存、finalize、verify、風格層（選用）、pytest（14 項）
- 備註：此機器 Word 的 `save as` 不可用，重存改用「開啟→標記已修改→close saving yes」
- 待做：網頁介面（目前僅 CLI）
