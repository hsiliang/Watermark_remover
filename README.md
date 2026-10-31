# Watermark_remover（自用，不公開）

檢測並消除 Claude 生成之 .docx 中的隱藏 AI 痕跡；用於對偵測系統做盲測。

## 用法
```
pip3 install -r requirements.txt
python3 -m wmr scan 檔案.docx            # 只檢測
python3 -m wmr clean 檔案.docx -o out/乾淨.docx          # 純本地重建
python3 -m wmr clean 檔案.docx -o out/乾淨.docx --word   # 再用 Word for Mac 重存（建議）
```
最後一步（`finalize`）永遠是純本地腳本：作者／最後修改者＝梁曦414155039，公司＝天主教輔仁大學。

## 流程
scan → rebuild（白名單重建）→ [Word for Mac 重存] → finalize（改屬性、ZIP 外觀）→ 複掃 → 保真比對

## 狀態
- 已完成：scan、rebuild、finalize、verify、CLI、pytest（6 項）
- 待處理：Word for Mac 自動化需在 Word 授權檔案存取後才能驗證
