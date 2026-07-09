# 過去問 自動答え合わせツール

自分で作った公開サイトの4択過去問（ア/イ/ウ/エ）を、ブラウザ自動操作で読み込み、
Gemini（無料枠）に解かせて、正解の選択肢を自動でクリックする学習補助ツールです。

> 自分の学習（過去問の答え合わせ）用の補助ツールです。他者が運営する試験の不正回答には使わないでください。

## しくみ

```
run.bat 起動
  → Python + Playwright がブラウザを起動
  → 指定URLの問題ページを開く
  → 問題エリア(表・図を含む)をスクショ + テキスト抽出
  → Gemini(gemini-2.5-flash)に送って ア/イ/ウ/エ を判定
  → 対応するラジオボタンにカーソルを動かしてクリック
  → 結果をコンソールに一覧表示
```

## 必要なもの

- Windows PC
- Python 3.10 以上（[python.org](https://www.python.org/downloads/) からインストール。インストール時に **"Add Python to PATH"** に必ずチェック）
- Gemini の API キー（無料）: [Google AI Studio](https://aistudio.google.com/apikey) で取得

## セットアップ（初回だけ）

1. このフォルダ内の `.env.example` をコピーして、ファイル名を `.env` に変更する
2. `.env` をメモ帳などで開き、次の2つを設定する
   - `GEMINI_API_KEY` … 取得したAPIキー
   - `QUIZ_URL` … 問題ページのURL
   - （ログインが必要なサイトなら、後述の「ログインが必要なサイトの場合」も設定する）
3. `run.bat` をダブルクリック
   - 初回だけ、自動で環境構築（仮想環境作成・ライブラリ・ブラウザのインストール）が走ります（数分）

## 使い方

- `run.bat` をダブルクリックするだけ
- ブラウザが立ち上がり、赤い点（カーソル）が動いてラジオボタンを選択します
- 終わると結果一覧が表示され、Enter で閉じます

### コマンドの追加オプション

```
run.bat --url https://example.com/quiz2   （URLをその場で指定）
run.bat --headless                          （ブラウザを表示せず裏で実行）
run.bat --no-submit                         （採点ボタンを押さない）
```

## ログインが必要なサイトの場合（自分のサイト用）

問題ページを開く前にログインが必要な場合は、`.env` に次を設定してください。
**`LOGIN_URL` / `LOGIN_USER` / `LOGIN_PASS` の3つがすべて埋まっているとログインを実行**します（1つでも空ならログインはスキップ）。

```
LOGIN_URL=https://あなたのサイト/login
LOGIN_USER=あなたのユーザー名
LOGIN_PASS=あなたのパスワード
```

ログイン後、同じブラウザのまま `QUIZ_URL` の問題ページへ進むので、セッション（ログイン状態）はそのまま引き継がれます。

入力欄やボタンの位置が既定と違う場合は、次のセレクタを実際のHTMLに合わせて変更してください。

| 項目 | 説明 | 既定値 |
|------|------|--------|
| `LOGIN_USER_SELECTOR` | ユーザー名の入力欄 | `input[name=username]` |
| `LOGIN_PASS_SELECTOR` | パスワードの入力欄 | `input[type=password]` |
| `LOGIN_BUTTON_SELECTOR` | ログインボタン | `button[type=submit]` |
| `LOGIN_SUCCESS_SELECTOR` | ログイン後だけ現れる要素（成功確認用。任意） | （空＝確認しない） |

> `LOGIN_SUCCESS_SELECTOR` を設定しておくと、ログインに失敗したとき（パスワード違いなど）にその場で気づけるのでおすすめです。

> ⚠️ `.env` にはパスワードが平文で入ります。`.env` は `.gitignore` 済みで共有されませんが、PCの取り扱いには注意してください。**このログイン機能は自分自身のサイト／アカウント用です。**

## 調整用の設定（.env）

| 項目 | 説明 |
|------|------|
| `GEMINI_MODEL` | 使用モデル（既定 `gemini-2.5-flash`） |
| `HEADLESS` | `true` でブラウザ非表示 |
| `SLOW_MO_MS` | 動作の遅延（見やすさ調整） |
| `QUESTION_SELECTOR` | 問題全体を囲む要素（既定 `tui-section-question`） |
| `FORM_SELECTOR` | 解答欄（既定 `tui-single-select-form`） |
| `RADIO_NAME` | ラジオの name（既定 `answer-option`） |
| `SUBMIT_SELECTOR` | 採点/送信ボタン（使うときだけ設定） |

## 注意点

- Gemini の解答は **100%正解ではありません**。答え合わせの参考として使ってください。
- サイトのHTML構造が想定と違う場合は、`.env` のセレクタを実際の構造に合わせて変更してください。
```
