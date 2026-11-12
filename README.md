# CLANNAD-Steam-JP

Steam版『CLANNAD』（SiglusEngine_Steam.exe）を日本語でプレイするための日本語化ツールおよびプロキシDLLです。

![CLANNAD Screenshot 1](https://madotsukanai.github.io/clannad.png)
![CLANNAD Screenshot 2](https://madotsukanai.github.io/clannad2.png)

ゲーム内に残存している日本語テキスト・リソースの復元、不足している日本語UI画像の生成、および実行時ファイルリダイレクト・UI動的翻訳を行う `steam_api.dll` プロキシを提供します。

---

## 主な機能

- **リソースの復元と日本語化 (`install_jp.py`)**
  - `GameexeZH.dat` を展開・変換し、日本語設定を含む `GameexeJP.dat` を生成
  - `dat/*.dbs`（テキストおよび設定テーブル）内の日本語データを復元し `dat_jp/` に配置
  - `g00` フォルダ内の日本語オリジナルグラフィックを `g00_jp/` に復元
  - 中国語版で画像化されていた設定画面等のUIボタン・ラベルを Pillow を用いて日本語版UI画像として合成・生成
  - 日本語フォント（`NotoSansMonoCJKjp-Regular.otf`）の自動ダウンロード・配置

- **プロキシDLL (`steam_api.dll` / `steam_proxy.c`)**
  - 元の `steam_api.dll`（`steam_api_original.dll`）のAPI呼び出しを透過的に中継
  - ゲームエンジンによるファイルアクセスを `g00_jp/`、`dat_jp/`、`GameexeJP.dat`、日本語フォントへ自動リダイレクト
  - Win32 CBTフック（`WH_CBT`）およびAPIフックを用い、各種設定ダイアログ（環境設定、ムービー再生設定、フォント設定など）のUIテキストを日本語に動的置換
  - 起動時の言語選択ダイアログ、または環境変数／起動引数による日本語・中国語の切り替え

---

## 動作環境

- **OS**: Windows または Linux (Proton)
- **ゲーム**: Steam版『CLANNAD』（中国語版）
- **Python**: 3.11 以上
  - `Pillow`（UI画像生成に必要）
    ```bash
    python -m pip install Pillow
    ```
  - オープニングムービーの置換には `yt-dlp` と JavaScript ランタイムも必要です。`yt-dlp[default]` にはYouTubeのJSチャレンジ用コンポーネントが含まれます。
    ```bash
    # Windows
    py -3.11 -m pip install --upgrade "yt-dlp[default]" imageio-ffmpeg

    # Linux
    python3.11 -m pip install --upgrade "yt-dlp[default]" imageio-ffmpeg
    ```
    さらに [Deno 2.3.0 以上](https://docs.deno.com/runtime/getting_started/installation/)をインストールして PATH に追加してください。`imageio-ffmpeg` の代わりに、システムの `ffmpeg` を PATH にインストールしても使用できます。
- **ビルドツール**（`steam_api.dll` をソースからビルドする場合）:
  - `make`
  - `i686-w64-mingw32-gcc` (MinGW-w64 32bit)

---

## インストール手順

> [!NOTE]
> UI画像の合成に `Pillow` を使用します。未インストールの場合はあらかじめ導入してください。
> ```bash
> python -m pip install Pillow
> ```
>
> オープニングムービーも日本語版に置き換える場合は、追加で `yt-dlp[default]`、Deno 2.3.0 以上、および `ffmpeg` が必要です。
> ```bash
> # Windows
> py -3.11 -m pip install --upgrade "yt-dlp[default]" imageio-ffmpeg
>
> # Linux
> python3.11 -m pip install --upgrade "yt-dlp[default]" imageio-ffmpeg
> ```
> Deno は[公式手順](https://docs.deno.com/runtime/getting_started/installation/)でインストールし、PATH に追加してください。`imageio-ffmpeg` を使わず、システムの `ffmpeg` を PATH に通しても構いません。ムービー取得時は `install_jp.py` の実行にも同じ Python 3.11 以上を指定してください（Windows: `py -3.11 install_jp.py`、Linux: `python3.11 install_jp.py`）。

### Windows の場合 (PowerShell / コマンドプロンプト)

```powershell
curl.exe -sLO https://github.com/Madotsukanai/CLANNAD-Steam-JP/raw/refs/heads/main/install_jp.py
py -3.11 install_jp.py
```

※ゲームのインストール先がデフォルトと異なる場合は、パスを引数に指定して実行してください：
```powershell
py -3.11 install_jp.py "C:\Program Files (x86)\Steam\steamapps\common\CLANNAD"
```

### Linux の場合 (Proton / Steam Deck)

```bash
curl -sLO https://github.com/Madotsukanai/CLANNAD-Steam-JP/raw/refs/heads/main/install_jp.py
python3.11 install_jp.py
```

※パスを指定して実行する場合：
```bash
python3.11 install_jp.py ~/.local/share/Steam/steamapps/common/CLANNAD
```

### オープニングムービーについて

`install_jp.py` は `yt-dlp` でオープニングムービーを取得し、`ffmpeg` でゲーム用の WMV に変換して `mov_jp/` に配置します。YouTubeの仕様変更で取得に失敗する場合は、上記のとおり新しいPython・`yt-dlp[default]`・Denoを使用してください。必要なツールが見つからない場合はムービーの置換だけをスキップし、それ以外の日本語化は続行します。`mov_jp/OP00.wmv` と `mov_jp/OP00_HIGH.wmv` が両方存在する場合も、再取得・変換をスキップします。

---

### (開発者向け) プロキシDLLのソースビルド

リポジトリ直下で `make` を実行すると `steam_api.dll` が生成されます。
```bash
make
```

---

## 起動方法と言語切り替え

インストール後、Steamから通常通りゲームを起動すると、初回起動時に言語選択ダイアログが表示されます。

- **「はい」**: 日本語版（`g00_jp` / `dat_jp`）で起動
- **「いいえ」**: 中国語版（オリジナル）で起動
- **「キャンセル」**: 起動を中止

### 起動オプション（ダイアログをスキップする場合）

Steamの「CLANNAD」のプロパティ →「起動オプション」に以下を指定することで、起動時の選択ダイアログをスキップして直接起動できます。

- **日本語版で起動**:
  ```text
  %command% --clannad-jp
  ```
  または環境変数:
  ```text
  clannad_lang=jp %command%
  ```

- **中国語版（元データ）で起動**:
  ```text
  %command% --clannad-cn
  ```
  または環境変数:
  ```text
  clannad_lang=cn %command%
  ```

---

## アンインストール（元の状態に戻す）

日本語化を解除し、元のファイル構成に戻すには `--remove` オプションを指定して実行します。

```bash
python3 install_jp.py --remove
```
バックアップされていた `steam_api_original.dll` が `steam_api.dll` に復元され、生成された `GameexeJP.dat`、`dat_jp/`、`g00_jp/` が削除されます。

---

## リポジトリ構成

```text
├── install_jp.py      # 日本語化適用・リソース生成スクリプト
├── steam_proxy.c      # steam_api.dll プロキシ本体（リダイレクト & Win32 UI翻訳）
├── proxy_stubs.S      # Steam API スタブ関数群
├── steam_api.def      # steam_api.dll エクスポート定義
├── Makefile           # steam_api.dll ビルド用Makefile
└── README.md          # 本ドキュメント
```

---

## ライセンス・権利表記

### 本プロジェクト
- 本ツールのソースコードは MIT License の下で公開されています。

### 使用・参照リソース
- **Noto Sans CJK (Noto Sans Mono CJK JP)**: [SIL Open Font License 1.1](http://scripts.sil.org/OFL)
- **Pillow**: [HPND License](https://github.com/python-pillow/Pillow/blob/main/LICENSE)

### 免責事項 (Disclaimer)
- 本プロジェクトは有志による非公式のファンメイドツールであり、公式の権利者（株式会社ビジュアルアーツ、Key、および Valve Corporation）とは一切関係ありません。
- 本ツールの利用には Steam 版『CLANNAD』の正規購入が必要です。ゲーム本体の著作権で保護されたデータ自体の再配布は行っておりません。
- 本ツールの使用によって生じたいかなる損害や問題についても、制作者は責任を負いかねます。自己責任にてご利用ください。
