# 改善清單狀態播報

2026-10-24 查核搜尋移除最後一列時的空清單訊息；保留目前 React／Vite，不新增套件。

| 來源 | 機制、授權與判斷 |
| --- | --- |
| [W3C ARIA22](https://www.w3.org/WAI/WCAG21/Techniques/aria/ARIA22) | 狀態容器須先於訊息存在，使用 `role=status` 與明示 `aria-atomic=true`。採用這項介面設計。 |
| [React Spectrum LiveAnnouncer](https://github.com/adobe/react-spectrum/blob/73d8be4/packages/@react-aria/live-announcer/src/LiveAnnouncer.tsx) | `LiveAnnouncer` 先建立 polite／assertive 容器，`announce` 再加入訊息；已核對 Apache-2.0 原始碼 header 及 LICENSE。僅參考容器生命週期，不複製實作；此處只有元件內的單一狀態，無需全域廣播佇列或新依賴。 |

查詢涵蓋狀態訊息標準與現有開源播報機制。將空清單標題放入持續掛載、視覺隱藏的狀態區，資料未就緒、錯誤或 deferred 篩選重算中保持空白；完成且無可見列才更新。檢查最後一列消失、原因改變、清除搜尋後的容器持續存在與焦點保留；DOM／ARIA 驗證不等於已測所有螢幕閱讀器。
