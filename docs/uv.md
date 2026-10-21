# uv 快速上手

本專案用 [uv](https://docs.astral.sh/uv/) 管理 Python 版本、虛擬環境和依賴，取代原本的 `python -m venv` 加 `pip install -r requirements.txt`。uv 會自動下載所需的 Python，不需要先自己安裝 Python。

## 安裝 uv

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
```

```powershell
# Windows（PowerShell）
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

也可以用 `winget install --id=astral-sh.uv -e`、`brew install uv` 或 `pipx install uv` 安裝。裝好後執行 `uv --version` 確認。

## 日常使用

在 repo 根目錄執行：

| 要做的事 | 指令 | 以前的做法 |
| --- | --- | --- |
| 建立環境並安裝依賴 | `uv sync` | `python -m venv .venv` 加上 `pip install -r requirements.txt` |
| 執行腳本 | `uv run scripts/update_data.py` | `.venv/bin/python scripts/update_data.py` |
| 執行模組或測試 | `uv run python -m unittest discover -s tests -p test_update_data.py -v` | `.venv/bin/python -m unittest ...` |
| 只裝資料管線的依賴（不含 `research` 群組，CI 用這個） | `uv sync --no-default-groups` | — |

- `uv run` 執行前會自動檢查環境是否和 `uv.lock` 一致，不一致時會先同步，所以通常不必手動啟用（activate）虛擬環境。
- 想啟用也可以：macOS／Linux 用 `source .venv/bin/activate`，Windows 用 `.venv\Scripts\activate`。

## 相關檔案

| 檔案 | 用途 |
| --- | --- |
| `pyproject.toml` | 宣告依賴；研究腳本另外需要的套件放在 `research` 依賴群組（目前只有 numpy，而 numpy 本來就是 shapely 的依賴），`uv sync` 預設會安裝 |
| `uv.lock` | 鎖定所有套件的確切版本。**要和 `pyproject.toml` 一起 commit** |
| `.python-version` | 指定 Python 版本（3.14，和 CI 相同） |
| `.venv/` | uv 建立的虛擬環境，不納入版控 |

## 新增或更新依賴

```bash
uv add 套件名稱                  # 資料管線用的依賴
uv add --group research 套件名稱  # 只有研究腳本會用到的依賴
uv lock --upgrade-package 套件名稱  # 升級單一套件
```

新增或升級後，`pyproject.toml` 和 `uv.lock` 會一起變動，兩個檔案都要 commit。CI 用 `uv sync --locked` 安裝，如果 lock 檔沒有更新，CI 會直接失敗。

## 不想用 uv 時

可以匯出 requirements 格式，再用 pip 安裝：

```bash
uv export --no-hashes -o requirements.txt
pip install -r requirements.txt
```

匯出的 `requirements.txt` 只供臨時使用，不要 commit；依賴以 `pyproject.toml` 和 `uv.lock` 為準。
