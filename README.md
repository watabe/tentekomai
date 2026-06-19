# tentekom.ai — キーワード入力型レポート生成AIエージェント

🌀 **tentekom.ai**（「てんてこ舞い」がモチーフ）は、キーワードを与えると
ローカル LLM（**LM Studio / llama.cpp** などの OpenAI 互換 API）で
情報収集 → 構成 → 生成 → 品質チェック → **HTML 出力**までを自動化する CLI ツールです。

設計は [要件定義](tmp/prompt_result.md) に準拠し、ローカル LLM で起きやすい
「出力切れ・重複・構成崩れ」を避けるため **パイプライン型 + 分割生成 + 中間保存 + 再開** を採用しています。

## 2つのレポート形式

`--type` によって生成パスが切り替わります。

| 形式 | `--type` | 中身 |
| --- | --- | --- |
| **ニュースダイジェスト型** | `news` | 多数のニュースをカテゴリ別に列挙。各ニュースは「サムネ画像＋見出し＋2〜3文要約＋出典リンク」。具体的なニュースをたくさん見せたいとき向け |
| **エッセイ（調査）型** | `research` / `comparison` / `proposal` / `tech` | アウトライン→章ごと長文→品質チェック→統合。根拠付きの読み物レポート |

## 特徴

- **OpenAI 互換 API に接続**（接続先は起動時に `--provider` で選択、または `--base-url` で直接指定）
- **SearXNG で情報収集**（テキスト＋画像）
- **ニュースダイジェスト**：検索クエリをカテゴリにして多数のニュースを列挙。各記事の **og:image をサムネ**として表示（無ければ favicon にフォールバック）。**鮮度フィルタ**（既定で直近1ヶ月、`--time-range` で調整）と、`--audience consumer`/`beginner` 時の**柔らかい文体**に対応。news では報道メディアを優先し、古い政府・調査PDFは降格
- **エッセイ型**：章ごと生成（800〜1,500字単位）＋ `finish_reason=length` の**出力切れ検知と継続生成**＋品質チェック（出力切れ / 文字数 / 根拠引用 / 重複 / 事実性）
- **スマホ向け PDF 出力**（`--format pdf` / `all`）：画像を base64 で**焼き込み**、**全体を1枚の縦長ページ**（スクロール閲覧でき、ページ分割の余白が出ない）で生成。スマホでローカル HTML を開くと外部画像を取得できない問題を回避できます（画像は幅いっぱいに縮小埋め込み、PDF は数 MB 程度。内容が極端に長い場合のみ自動でページ分割にフォールバック）
- **思考（reasoning）型モデル対応**：`reasoning_content` 分離・`<think>` 除去・部分 JSON 救出・トークン上限を大きめに設定
- **中間成果物を全保存**し、失敗時は `resume` で**ステップ／章単位で再開**
- LLM 呼び出しログ（prompt / response / token / 時間）を JSONL で記録
- 生成 HTML に **tentekom.ai のロゴ（回転ピンウィール）とファビコン**を埋め込み

## システム構成

本ツール（CLI）は単体では完結せず、ローカルで動く **2つのサービス**（LLM と検索）に接続します。
さらに news の鮮度判定・サムネ取得のため、各記事サイトへ HTTP アクセスします。

```
            ┌──────────────────────────────────────────────────┐
            │              あなたのマシン (ローカル)               │
            │                                                  │
keyword ──▶ │  ┌─────────────┐   OpenAI互換API                  │
            │  │ tentekom.ai │ ──/v1/chat/completions──▶  ローカルLLM
            │  │  (この CLI) │ ◀──────── 生成テキスト ────  LM Studio :1234
            │  │   Python    │                            または llama.cpp :8080
            │  │             │   検索 (JSON API)                 │
            │  │             │ ──/search?format=json──▶  SearXNG :8080 (Docker)
            │  │             │ ◀──────── 検索結果(JSON) ──        │
            │  └─────┬───────┘                                  │
            │        │ 各記事ページを取得 (og:image・公開日 meta)   │
            │        └────────────────────────────▶  Web(各ニュースサイト)
            │                                                  │
            │  出力 → reports/<日付_キーワード>/output/            │
            │          report.md / report.html / report.pdf    │
            └──────────────────────────────────────────────────┘
```

| コンポーネント | 役割 | 既定エンドポイント | 必須 |
| --- | --- | --- | --- |
| **tentekom.ai（本体）** | パイプライン制御・生成・出力 | — | ✅ |
| **ローカル LLM** | 計画／要約／本文生成（OpenAI 互換） | LM Studio `:1234`／llama.cpp `:8080` | ✅ |
| **SearXNG** | Web 検索（テキスト・画像） | `:8080`（Docker） | 検索利用時（`--no-search` なら不要） |
| Web（各記事サイト） | サムネ（og:image）・公開日の取得 | — | news のサムネ／鮮度判定で使用 |

> ⚠️ **ポート注意**: llama.cpp と SearXNG はどちらも既定が `8080`。同時利用ならどちらかをずらします
> （本 README の例は **SearXNG=8080 / LM Studio=1234**）。

## セットアップ

### 1. 本体（Python CLI）

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt   # requests / PyYAML / markdown / weasyprint / Pillow
```

> PDF 出力に使う **weasyprint** は環境によっては system ライブラリ（pango / cairo など）が必要です。
> macOS は `brew install pango`、Linux は各ディストリの weasyprint ドキュメントを参照。
> PDF が不要なら `--format html`（既定）のままで weasyprint は使われません。

### 2. ローカル LLM（いずれか）

- **LM Studio**: モデルをロード → ローカルサーバを起動 → `http://localhost:1234/v1`
- **llama.cpp**: `llama-server -m model.gguf --port 8080` → `http://127.0.0.1:8080/v1`

> **モデル選びの注意（低スペック環境）**：本ツールは1レポートで多数の LLM 呼び出しを行います。
> 27B 級の密モデルや思考が長いモデルは遅く不安定になりがちです。検証環境では
> 軽量・高速な **`google/gemma-4-e4b`（約34 tok/s）** が安定でした。

### 3. SearXNG（検索バックエンド／Docker 推奨）

検索を使うなら SearXNG が必要です。**本ツールは JSON API を叩くため、SearXNG 側で
JSON 出力を有効化する必要があります**（既定は無効で、`format=json` は **403** になります）。

```bash
# 1) 起動（設定は ./searxng にマウント。初回起動で settings.yml が生成される）
docker run -d --name searxng -p 8080:8080 \
  -v "$PWD/searxng:/etc/searxng" \
  searxng/searxng:latest
```

生成された `settings.yml`（マウント先 `./searxng/settings.yml`、または
`docker exec searxng vi /etc/searxng/settings.yml`）の `search.formats` に **`json` を追加**します：

```yaml
search:
  formats:
    - html
    - json          # ← これを追加（本ツールに必須。既定は html のみ）

server:
  # secret_key は公式イメージなら初回起動時に自動生成される（手動運用時のみ設定）。
  # 自動アクセスが 429/403 で弾かれる場合は limiter を無効化:
  # limiter: false
```

```bash
# 2) 反映
docker restart searxng

# 3) 確認（200 と JSON が返れば OK）
curl -s "http://localhost:8080/search?q=test&format=json" -o /dev/null -w "%{http_code}\n"
```

> **うまくいかない時**
> - **403 / HTML が返る**: `search.formats` に `json` が無い（最頻）。`docker restart` 忘れ。
>   または `limiter` が有効でアクセスが弾かれている → `limiter: false`。
> - **0 件ばかり**: `--time-range` が厳しすぎる可能性（`day`→`week`/`month` で緩める）。

実行時は `--searxng-url http://localhost:8080` を渡します。

## 動作チェックリスト（別環境セットアップ時）

新しい環境に入れたら、上から順に確認すると確実です。各行のコマンドが「期待」どおりなら OK。

| # | 確認項目 | コマンド | 期待 |
| --- | --- | --- | --- |
| 1 | Python 3.10+ | `python3 --version` | `3.10` 以上 |
| 2 | 依存導入済み | `.venv/bin/python -c "import requests,yaml,markdown,weasyprint,PIL;print('ok')"` | `ok`（失敗なら `pip install -r requirements.txt`） |
| 3 | CLI 起動 | `./report-agent providers` | プロバイダ一覧が出る |
| 4 | LLM 疎通 | `curl -s http://localhost:1234/v1/models -o /dev/null -w "%{http_code}\n"` | `200`（LM Studio/llama.cpp が起動・モデルロード済み） |
| 5 | SearXNG＋JSON | `curl -s "http://localhost:8080/search?q=test&format=json" -o /dev/null -w "%{http_code}\n"` | `200`（`403`/HTML なら `search.formats` に `json` 未設定 → 上の「3. SearXNG」参照） |
| 6 | ポート非衝突 | （4と5のホスト/ポートが別） | LLM と SearXNG が別ポート |
| 7 | オフラインテスト | `.venv/bin/python tests/test_offline.py` | `すべてのオフラインテストに合格しました。`（サーバ不要） |
| 8 | PDF 依存 | `.venv/bin/python -c "import weasyprint;weasyprint.HTML(string='<h1>ok</h1>').write_pdf('/tmp/_t.pdf');print('pdf ok')"` | `pdf ok`（PDF 不要なら省略可） |
| 9 | スモーク実行 | 下の「使い方」のニュース例を1本実行 | `reports/<日付_…>/output/` に `report.pdf` 等が生成 |

### 一括プリフライト（コピペで疎通確認）

外部サービス（依存・LLM・SearXNG）をまとめて確認します。URL は環境に合わせて変更してください。

```bash
PY=.venv/bin/python
LLM=http://localhost:1234        # LM Studio。llama.cpp なら http://127.0.0.1:8080
SX=http://localhost:8080         # SearXNG

$PY -c "import requests,yaml,markdown,weasyprint,PIL" 2>/dev/null \
  && echo "deps      : OK" || echo "deps      : NG -> pip install -r requirements.txt"

curl -fsS "$LLM/v1/models" >/dev/null 2>&1 \
  && echo "LLM       : OK" || echo "LLM       : NG -> $LLM が起動&モデルロード済みか確認"

code=$(curl -s "$SX/search?q=test&format=json" -o /tmp/sx.json -w "%{http_code}")
if [ "$code" = "200" ] && grep -q '"results"' /tmp/sx.json; then
  echo "SearXNG   : OK"
else
  echo "SearXNG   : NG (HTTP $code) -> settings.yml の search.formats に json を追加し docker restart"
fi

$PY tests/test_offline.py >/dev/null 2>&1 \
  && echo "offline   : OK" || echo "offline   : NG -> tests/test_offline.py の出力を確認"
```

> すべて `OK` なら、下の「使い方」のスモーク実行に進めます。`--no-search` を付ければ SearXNG 無しでも
> LLM とパイプラインだけを試せます（検索が不要な疎通確認に便利）。

## 使い方

```bash
# プロバイダ一覧
./report-agent providers

# ニュースダイジェスト（推奨フロー）
# ガジェット等の消費者向け製品ニュースは --audience consumer を付けると
# 検索クエリが製品・ブランド寄り(スマートグラス/iPhone/Galaxy 等)になります。
./report-agent run "2026年6月 ガジェット ニュース 新製品" \
    --provider lmstudio \
    --model "google/gemma-4-e4b" \
    --type news --audience consumer \
    --searxng-url http://localhost:8080

# エッセイ（調査レポート）を生成
./report-agent run "ローカルLLM エージェント 比較" \
    --provider lmstudio --model "google/gemma-4-e4b" \
    --type research --length 5000 \
    --searxng-url http://localhost:8080

# 検索なし（LLM の知識のみ）
./report-agent run "2026年 生成AI トレンド" --provider lmstudio --model "google/gemma-4-e4b" --no-search

# 任意の互換エンドポイントを直接指定
./report-agent run "キーワード" --base-url http://192.168.1.10:8080/v1 --model my-model

# 途中で失敗したら再開（完了済みステップ／章はスキップ）
./report-agent resume reports/20260617_xxxxx
```

設定ファイルを使う場合は [`config.yaml.example`](config.yaml.example) をコピーして編集し、
`--config config.yaml` を渡します。**優先順位は CLI 引数 > 設定ファイル > プリセット既定値**。
`resume` は保存済み設定（`input.yaml`）を使うため、既定値を変えたいときは新規 `run` してください。

### 主なオプション（`run`）

| オプション | 説明 |
| --- | --- |
| `--provider` | `llama.cpp` / `lmstudio` / `custom` |
| `--base-url` | 互換 API の base_url（`--provider` より優先） |
| `--model` | モデル名（例 `google/gemma-4-e4b`） |
| `--type` | `news`（ダイジェスト）/ `research` / `comparison` / `proposal` / `tech` |
| `--audience` | `business` / `consumer`（一般消費者向け製品ニュース）/ `engineer` / `beginner` |
| `--length` | 目標総文字数（エッセイ型の章数の目安に使用） |
| `--format` | `html`（既定）/ `md` / `pdf` / `all` |
| `--searxng-url` | SearXNG の URL |
| `--time-range` | 鮮度フィルタ `day`/`week`/`month`/`year`/`off`（既定: news=直近1ヶ月、それ以外なし） |
| `--no-search` | 検索せず LLM の知識のみで生成 |
| `--force` | 接続確認に失敗しても続行 |

## 出力ディレクトリ

```
reports/<YYYYMMDD_キーワード>/
  input.yaml          実行パラメータ＋設定スナップショット
  state.json          完了ステップ（resume 用）
  research/
    plan.json         調査計画・検索クエリ
    sources.json      情報源（title/url/取得日時/信頼度/カテゴリ/サムネ）
    images.json       画像参照（ギャラリー用）
  outline.json        アウトライン（エッセイ型）
  chapters/           章本文とメタ（エッセイ型）
  digest.json         ニュース項目（news 型）
  review/issues.json  品質チェック結果（エッセイ型）
  output/
    report.md  report.html  report.pdf   # pdf は画像埋め込み・スマホ向け
  logs/llm_calls.jsonl  LLM 呼び出しログ
```

## アーキテクチャ

```
CLI ─ Pipeline ─┬ Planner    キーワード→調査方針・検索クエリ（audience で傾向を調整）
                ├ Researcher SearXNG で収集（テキスト/画像 + time_range 鮮度フィルタ）
                │
                ├─[news]→ Digest   各記事ページ取得（サムネ og:image + 公開日 meta）
                │                  → 鮮度で選別/並べ替え → クエリ別に分類 → 要約
                │
                └─[essay]→ Outliner → Writer → Reviewer
                                      （出力切れは継続生成 / 品質チェック）
                ↓
                Exporter   Markdown / HTML / PDF（ロゴ・画像・サムネ埋め込み）
```

単一プロセス内のステップ分割で、各ステップが中間成果物を保存します
（[pipeline.py](report_agent/pipeline.py) / 各 [agents/](report_agent/agents) / [search.py](report_agent/search.py) / [recency.py](report_agent/recency.py) / [thumbnails.py](report_agent/thumbnails.py) / [pdf.py](report_agent/pdf.py)）。

## テスト

LLM／検索サーバなしで全パイプライン（エッセイ型・ニュースダイジェスト型）を検証できます：

```bash
.venv/bin/python tests/test_offline.py
```

## ロードマップ

**実装済み**

- ✅ CLI + SearXNG 検索 + Markdown/HTML 出力
- ✅ ニュースダイジェスト型（クエリ別カテゴリ・多数列挙）／エッセイ型（章分割・品質チェック）
- ✅ 記事サムネ（og:image、小画像は除外して favicon フォールバック）＋ tentekom.ai ロゴ・favicon
- ✅ スマホ向け PDF 出力（画像 base64 焼き込み・単一縦長ページ）
- ✅ 鮮度フィルタ（`--time-range` ＋ 記事 meta からの公開日抽出で「昨日のニュース」優先）
- ✅ 読者層に応じた調整（`--audience consumer/beginner` で製品・分野追従クエリ＋柔らかい文体）
- ✅ news モードの情報源ランキング（報道メディア優先・古い政府/調査PDF降格）
- ✅ 思考型モデル対応・出力切れ検知・中間保存＆`resume`

**今後の候補**

- ⏳ 定期実行（cron／スケジューラ連携）
- ⏳ 過去レポートの RAG（ベクトル DB）
- ⏳ 検索元の拡張（RSS／Web 検索 API／ローカルファイル）
- ⏳ ポータル/INDEX ページの除外精度向上（鮮度判定のノイズ低減）
- ⏳ 出力フォーマット追加（DOCX 等）
