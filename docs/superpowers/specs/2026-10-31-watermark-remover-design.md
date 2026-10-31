# Watermark_remover 設計

## 目的
紅隊工具：消除 Claude 生成之 .docx 中的隱藏 AI 痕跡，用來對同學的偵測系統做盲測。自用、不公開。

## 目標
- `scan`：列出所有可能的 AI／工具痕跡（分層、分嚴重度）。
- `rebuild`：純本地、確定性（無 AI）的白名單重建：只搬運內容與可見格式到全新封包，作者「梁曦414155039」、公司「天主教輔仁大學」。
- `word-normalize`（選用）：以 Word for Mac（AppleScript）重存，使結構像真正 Word 產物，再由本地腳本重寫屬性。
- `verify`：重建後再掃描，並比對文字／格式保真度。

## 痕跡分層
1. properties：core/app/custom 屬性（creator、application、template、description…）
2. package：customXml、webextensions、thumbnail、people、comments 作者、printerSettings
3. xml：rsid、proofErr、_GoBack、paraId、庫簽章（python-docx / docx-js 預設值）、隱藏文字
4. content：零寬／bidi／tag 字元、特殊空白
5. zip：時間戳、順序、create_system、壓縮、XML 宣告風格

## 非目標
- 不做文字改寫（風格層）；僅做不改語意的機械式字元正規化。（我的決定）

## 保真原則
document.xml 的可見格式（styles、numbering、theme、media）原樣保留，只剝除識別性屬性。
