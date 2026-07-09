# -*- coding: utf-8 -*-
"""
過去問 自動答え合わせツール
---------------------------------
1. 指定URLの問題ページをブラウザで開く
2. 問題エリア(表・図を含む)をスクリーンショット + テキスト抽出
3. Gemini(無料枠)に解かせて ア/イ/ウ/エ のどれかを判定
4. 対応するラジオボタンにカーソルを動かしてクリック
5. 結果をコンソールに表示

※ 自分の学習(過去問の答え合わせ)用の補助ツールです。
"""

import argparse
import json
import os
import re
import sys
import time

from dotenv import load_dotenv
from google import genai
from google.genai import types
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeoutError

# 選択肢の記号 → ラジオの並び順(0始まり)の対応
SYMBOLS = ["ア", "イ", "ウ", "エ"]


# ------------------------------------------------------------
# 設定の読み込み
# ------------------------------------------------------------
def load_config():
    load_dotenv()  # .env を読み込む
    parser = argparse.ArgumentParser(description="過去問 自動答え合わせツール")
    parser.add_argument("--url", help="問題ページのURL (.envのQUIZ_URLより優先)")
    parser.add_argument("--headless", action="store_true", help="ブラウザを表示しない")
    parser.add_argument("--no-submit", action="store_true", help="採点ボタンを押さない")
    parser.add_argument("--max", type=int, help="解く問題の最大数 (.envのMAX_QUESTIONSより優先)")
    parser.add_argument("--auto-assignments", action="store_true",
                        help="ホーム→学習トレーニング→課題…と課題を自動で巡回する")
    args = parser.parse_args()

    cfg = {
        "api_key": os.getenv("GEMINI_API_KEY", "").strip(),
        "url": (args.url or os.getenv("QUIZ_URL", "")).strip(),
        "model": os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip(),
        "headless": args.headless or os.getenv("HEADLESS", "false").lower() == "true",
        "slow_mo": int(os.getenv("SLOW_MO_MS", "400")),
        "question_selector": os.getenv("QUESTION_SELECTOR", "tui-section-question").strip(),
        "form_selector": os.getenv("FORM_SELECTOR", "tui-single-select-form").strip(),
        "radio_name": os.getenv("RADIO_NAME", "answer-option").strip(),
        "submit_selector": "" if args.no_submit else os.getenv("SUBMIT_SELECTOR", "").strip(),
        # 1問ごとに押す「回答」ボタン(空ならボタン文言から自動検出)
        "answer_button_selector": os.getenv("ANSWER_BUTTON_SELECTOR", "").strip(),
        # 回答後にフィードバック画面などで出る「次へ」ボタン(指定したときだけ押す)
        "next_selector": os.getenv("NEXT_SELECTOR", "").strip(),
        # 「次へ」ボタンを文言から自動で探して押すか(既定OFF。誤クリック防止のため)
        "auto_next": os.getenv("AUTO_NEXT", "false").lower() == "true",
        # ブラウザの確認ダイアログ(「中断しますか?」等)への対応: accept=OK / dismiss=キャンセル
        "dialog_action": os.getenv("DIALOG_ACTION", "accept").strip().lower(),
        # 解く問題数の上限(暴走防止のセーフティ)
        "max_questions": args.max or int(os.getenv("MAX_QUESTIONS", "200")),
        # 次の問題を待つ/解答欄を待つときのタイムアウト(ミリ秒)
        "question_timeout": int(os.getenv("QUESTION_TIMEOUT_MS", "20000")),
        # ===== 課題を自動で巡回するモード =====
        "auto_assignments": args.auto_assignments or os.getenv("AUTO_ASSIGNMENTS", "false").lower() == "true",
        # 自動巡回モードで最初に開く「ホーム画面」のURL(任意。空ならQUIZ_URLを使う)
        "home_url": os.getenv("HOME_URL", "").strip(),
        # 巡回で使う各ボタンの文言(サイトに合わせて変更可)
        "training_text": os.getenv("TRAINING_TEXT", "学習トレーニング").strip(),
        "assignment_menu_text": os.getenv("ASSIGNMENT_MENU_TEXT", "課題").strip(),
        "start_text": os.getenv("START_TEXT", "開始する").strip(),
        "grading_text": os.getenv("GRADING_TEXT", "答え合わせ").strip(),
        "back_to_list_text": os.getenv("BACK_TO_LIST_TEXT", "課題詳細画面へ").strip(),
        # 課題一覧の各項目のセレクタ(空なら未完了バッジから自動推定)
        "assignment_selector": os.getenv("ASSIGNMENT_SELECTOR", "").strip(),
        # 未完了を表す文言(この文言を含む項目を「これから解く課題」とみなす)
        "incomplete_text": os.getenv("INCOMPLETE_TEXT", "未完了").strip(),
        # 巡回する課題数の上限(暴走防止)
        "max_assignments": int(os.getenv("MAX_ASSIGNMENTS", "50")),
        # --- ログイン設定(自分のサイト用。空なら未使用) ---
        "login_url": os.getenv("LOGIN_URL", "").strip(),
        "login_user": os.getenv("LOGIN_USER", ""),
        "login_pass": os.getenv("LOGIN_PASS", ""),
        "login_user_selector": os.getenv("LOGIN_USER_SELECTOR", "input[name=username]").strip(),
        "login_pass_selector": os.getenv("LOGIN_PASS_SELECTOR", "input[type=password]").strip(),
        "login_button_selector": os.getenv("LOGIN_BUTTON_SELECTOR", "button[type=submit]").strip(),
        # 2段階ログイン用: ユーザー名を入れた後に押す「次へ」ボタン(任意)
        "login_next_selector": os.getenv("LOGIN_NEXT_SELECTOR", "").strip(),
        "login_success_selector": os.getenv("LOGIN_SUCCESS_SELECTOR", "").strip(),
    }
    # ログインを使うかどうか(URL・ユーザー・パスワードが揃っていれば有効)
    cfg["use_login"] = bool(cfg["login_url"] and cfg["login_user"] and cfg["login_pass"])

    # 自動巡回モードで HOME_URL が指定されていれば、最初に開くURLをそれにする
    if cfg["auto_assignments"] and cfg["home_url"]:
        cfg["url"] = cfg["home_url"]

    # 必須項目チェック
    problems = []
    if not cfg["api_key"] or cfg["api_key"].startswith("ここに"):
        problems.append("GEMINI_API_KEY が未設定です (.env を確認してください)")
    if not cfg["url"] or "example.com" in cfg["url"]:
        if cfg["auto_assignments"]:
            problems.append("最初に開くURLが未設定です (.env の HOME_URL か QUIZ_URL にホーム画面のURLを設定してください)")
        else:
            problems.append("QUIZ_URL が未設定です (.env を確認するか --url で指定してください)")
    if problems:
        print("[設定エラー]")
        for p in problems:
            print("  - " + p)
        sys.exit(1)

    # ログイン設定が一部だけ書かれている場合は注意を出す(ログインはスキップされる)
    partial = [cfg["login_url"], cfg["login_user"], cfg["login_pass"]]
    if any(partial) and not all(partial):
        print("[注意] LOGIN_URL / LOGIN_USER / LOGIN_PASS のうち一部だけが設定されています。")
        print("       3つすべてを設定しないとログインはスキップされます。\n")

    return cfg


# ------------------------------------------------------------
# 画面に見えるカーソル(赤い点)を注入する
# ------------------------------------------------------------
CURSOR_SCRIPT = """
() => {
  if (window.__fakeCursor) return;
  const dot = document.createElement('div');
  dot.id = '__fake_cursor';
  Object.assign(dot.style, {
    position: 'fixed', width: '18px', height: '18px', borderRadius: '50%',
    background: 'rgba(255,0,0,0.55)', border: '2px solid red',
    zIndex: 2147483647, pointerEvents: 'none', transform: 'translate(-50%,-50%)',
    transition: 'background 0.1s', left: '0px', top: '0px'
  });
  document.body.appendChild(dot);
  window.__fakeCursor = dot;
  document.addEventListener('mousemove', (e) => {
    dot.style.left = e.clientX + 'px';
    dot.style.top = e.clientY + 'px';
  });
}
"""


def install_cursor(page):
    try:
        page.evaluate(CURSOR_SCRIPT)
    except Exception:
        pass  # カーソル表示は失敗しても本処理には影響させない


# ------------------------------------------------------------
# 確認ダイアログ(「中断しますか?」など)を自動で処理する
# ------------------------------------------------------------
def install_dialog_handler(page, cfg):
    """alert / confirm / beforeunload を自動処理して、操作が止まらないようにする。

    - beforeunload(ページ離脱の確認)は常に承諾して、次の問題への遷移を通す。
    - それ以外の confirm/alert は DIALOG_ACTION 設定(既定 accept)に従う。
    これを入れないと「中断しますか?」等のダイアログでブラウザ操作が止まってしまう。
    """
    action = cfg.get("dialog_action", "accept")

    def handler(dialog):
        msg = (dialog.message or "").replace("\n", " ").strip()
        try:
            if dialog.type == "beforeunload":
                dialog.accept()  # 離脱を許可 = 次の問題へ進む
                print(f"  [ダイアログ] 離脱確認「{msg}」→ 承諾(次へ進みます)")
                return
            if action == "dismiss":
                dialog.dismiss()
                print(f"  [ダイアログ] 「{msg}」→ キャンセルを選びました")
            else:
                dialog.accept()
                print(f"  [ダイアログ] 「{msg}」→ OK を選びました")
        except Exception:
            # 既に閉じられている等は無視
            pass

    page.on("dialog", handler)


# ------------------------------------------------------------
# 1つの解答フォームぶんの情報を取り出す
# ------------------------------------------------------------
def extract_options(form):
    """フォーム内のラジオを [{index, value, symbol, input_locator}] で返す"""
    inputs = form.locator("input[type=radio]")
    count = inputs.count()
    options = []
    for i in range(count):
        inp = inputs.nth(i)
        value = inp.get_attribute("value") or ""
        # ラジオに紐づくラベル内の記号(ア/イ/ウ/エ)を取得
        label = inp.locator("xpath=ancestor::label[1]")
        symbol = ""
        # 1) class に "symbol" を含む要素があれば最優先で使う
        try:
            sym = label.locator("xpath=.//*[contains(@class,'symbol')]")
            if sym.count() > 0:
                symbol = (sym.first.inner_text() or "").strip()
        except Exception:
            pass
        # 2) なければラベル全体のテキストから ア/イ/ウ/エ を拾う
        if symbol not in SYMBOLS:
            try:
                txt = label.inner_text() or ""
            except Exception:
                txt = ""
            picked = next((c for c in txt if c in SYMBOLS), "")
            symbol = picked or symbol
        # 3) それでもダメなら並び順から補完
        if symbol not in SYMBOLS and i < len(SYMBOLS):
            symbol = SYMBOLS[i]
        options.append({"index": i, "value": value, "symbol": symbol, "input": inp})
    return options


def nearest_question_text(form, question_selector):
    """フォームに一番近い設問テキストを取得(なければ空文字)"""
    for sel in ["tui-question-content", "div.question", question_selector]:
        try:
            anc = form.locator(f"xpath=ancestor::{sel.split('.')[0]}[1]")
            if anc.count() > 0:
                txt = anc.first.inner_text().strip()
                if txt:
                    return txt
        except Exception:
            continue
    return ""


# ------------------------------------------------------------
# Gemini に解かせる
# ------------------------------------------------------------
def ask_gemini(client, model, image_bytes, question_text, options):
    symbols = [o["symbol"] or SYMBOLS[o["index"]] for o in options]
    prompt = f"""あなたは日本の試験問題を解く専門家です。
添付した画像は問題全体(表・図・グラフを含む)のスクリーンショットです。画像の内容を正確に読み取って解いてください。

【設問テキスト(参考)】
{question_text or "(画像を参照)"}

【選択肢】
この設問の選択肢は次の記号です: {", ".join(symbols)}
各記号が指す内容は画像内に書かれています。

【回答形式】
必ず次のJSONだけを出力してください。前後に説明文やコードブロック記号は付けないこと。
{{"answer": "<{'/'.join(symbols)} のいずれか1つ>", "reason": "<50字程度の根拠>"}}
"""
    contents = [
        types.Part.from_bytes(data=image_bytes, mime_type="image/png"),
        prompt,
    ]
    resp = _generate_with_retry(client, model, contents)
    text = (resp.text or "").strip()
    return parse_answer(text, symbols)


def _is_retryable_error(e):
    """Gemini の一時的なエラー(混雑・レート制限など)かどうか。"""
    code = getattr(e, "code", None) or getattr(e, "status_code", None)
    if code in (429, 500, 502, 503, 504):
        return True
    msg = str(e).upper()
    keywords = [
        "503", "502", "504", "429", "500",
        "UNAVAILABLE", "RESOURCE_EXHAUSTED", "INTERNAL",
        "OVERLOADED", "HIGH DEMAND", "TIMEOUT", "DEADLINE",
    ]
    return any(k in msg for k in keywords)


def _generate_with_retry(client, model, contents, max_attempts=6, base_delay=5):
    """generate_content を、一時的エラー時に待って再試行しながら呼ぶ。"""
    last_err = None
    for attempt in range(1, max_attempts + 1):
        try:
            return client.models.generate_content(model=model, contents=contents)
        except Exception as e:  # noqa: BLE001
            last_err = e
            if attempt >= max_attempts or not _is_retryable_error(e):
                raise
            delay = min(base_delay * (2 ** (attempt - 1)), 60)
            print(
                f"  [再試行] Geminiが混雑しています(試行 {attempt}/{max_attempts})。"
                f"{delay}秒待って再試行します..."
            )
            time.sleep(delay)
    # ここには来ないが保険
    if last_err:
        raise last_err


def parse_answer(text, symbols):
    """Geminiの返答から記号と理由を取り出す"""
    answer, reason = "", ""
    # まずJSONとして解釈を試みる
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            data = json.loads(m.group(0))
            answer = str(data.get("answer", "")).strip()
            reason = str(data.get("reason", "")).strip()
        except Exception:
            pass
    # ダメなら本文から最初に出てくる記号を拾う
    if answer not in symbols:
        for ch in text:
            if ch in symbols:
                answer = ch
                break
    if answer not in symbols:
        answer = ""  # 判定不能
    return answer, reason, text


# ------------------------------------------------------------
# カーソルを動かしてクリック
# ------------------------------------------------------------
def move_and_click(page, target_locator):
    target_locator.scroll_into_view_if_needed()
    box = target_locator.bounding_box()
    if not box:
        # 座標が取れない場合は通常クリックにフォールバック
        target_locator.click(force=True)
        return
    x = box["x"] + box["width"] / 2
    y = box["y"] + box["height"] / 2
    # 現在位置から目標まで滑らかに移動(人間っぽく)
    page.mouse.move(x, y, steps=30)
    time.sleep(0.3)
    page.mouse.click(x, y)


# ------------------------------------------------------------
# 自分のサイトへログインする
# ------------------------------------------------------------
def _first_visible(page, selector):
    """selector に一致する要素が1つ以上あり、先頭が実際に表示されているか。"""
    if not selector:
        return False
    loc = page.locator(selector)
    try:
        return loc.count() > 0 and loc.first.is_visible()
    except Exception:
        return False


def _click_button_or_enter(page, cfg, field_selector, label):
    """ログインボタンがあれば押す。無ければ field_selector で Enter を押して代用する。

    サイトによって送信ボタンの形が違ったり、そもそもボタンが無く Enter 送信のことも
    あるため、両対応にしておく。
    """
    if _first_visible(page, cfg["login_button_selector"]):
        print(f"  {label} ログインボタンを押します")
        move_and_click(page, page.locator(cfg["login_button_selector"]).first)
        return
    # ボタンが見つからない/非表示 → 入力欄で Enter を押して送信を試みる
    try:
        print(f"  {label} ボタンが無いため {field_selector} で Enter を押します")
        page.press(field_selector, "Enter")
    except Exception:
        pass


def _advance_to_password(page, cfg):
    """2段階ログイン用: ユーザー名の次画面(パスワード欄)へ進める。

    1) LOGIN_NEXT_SELECTOR が指定されていればそのボタンを押す
    2) 指定がなければ、表示中のログインボタン(=この時点では「次へ」兼用)を押す
    3) それも無ければユーザー名欄で Enter を押す
    """
    if cfg["login_next_selector"]:
        nb = page.locator(cfg["login_next_selector"])
        if nb.count() > 0:
            print("  [2段階] 「次へ」ボタンを押してパスワード欄を表示します")
            move_and_click(page, nb.first)
            return
        print(f"  [注意] LOGIN_NEXT_SELECTOR({cfg['login_next_selector']})が見つかりませんでした。別の方法を試します。")

    _click_button_or_enter(page, cfg, cfg["login_user_selector"], "[2段階]")


def _login_form_present(page, cfg):
    """今のページにログインフォーム(ユーザー名 or パスワード欄)が見えているか。"""
    return _first_visible(page, cfg["login_user_selector"]) or _first_visible(
        page, cfg["login_pass_selector"]
    )


def _perform_login_on_page(page, cfg):
    """今表示されているページに対してユーザー名・パスワードを入力して送信する。

    ページ遷移(goto)はしない。LOGIN_URL でも、問題ページで出たログイン画面でも使える。
    """
    install_cursor(page)

    # ユーザー名(表示されるのを待ってから入力)
    try:
        page.wait_for_selector(cfg["login_user_selector"], state="visible", timeout=15000)
        page.fill(cfg["login_user_selector"], cfg["login_user"])
    except PWTimeoutError:
        print(f"  [エラー] ユーザー名入力欄({cfg['login_user_selector']})が見つかりませんでした。")
        print("          .env の LOGIN_USER_SELECTOR を確認してください。")
        raise

    pass_sel = cfg["login_pass_selector"]

    # パスワード欄がまだ表示されていなければ、「次へ」を押して出す(2段階ログイン)
    if not _first_visible(page, pass_sel):
        _advance_to_password(page, cfg)

    # パスワード欄が「表示される」まで待つ(DOMにあっても非表示なら入力できないため)
    try:
        page.wait_for_selector(pass_sel, state="visible", timeout=20000)
    except PWTimeoutError:
        print(f"  [エラー] パスワード入力欄({pass_sel})が表示されませんでした。")
        print("          2段階ログインの場合は .env の LOGIN_NEXT_SELECTOR に")
        print("          「次へ」ボタンのセレクタを設定してください。")
        print("          また LOGIN_PASS_SELECTOR が正しいかも確認してください。")
        raise

    # パスワード
    page.fill(pass_sel, cfg["login_pass"])

    # 送信: ログインボタンがあれば押す。無ければパスワード欄で Enter を押して送信する
    _click_button_or_enter(page, cfg, pass_sel, "[送信]")


def _verify_login(page, cfg):
    """ログイン成功の確認。LOGIN_SUCCESS_SELECTOR があればそれを待つ。"""
    if cfg["login_success_selector"]:
        try:
            page.wait_for_selector(cfg["login_success_selector"], timeout=20000)
        except PWTimeoutError:
            print(f"  [エラー] ログイン後の目印({cfg['login_success_selector']})が現れませんでした。")
            print("          ユーザー名/パスワード、または LOGIN_SUCCESS_SELECTOR を確認してください。")
            raise RuntimeError("login verification failed")
    else:
        # 目印がなければ、ページの読み込みが落ち着くのを待つだけ(best-effort)
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except PWTimeoutError:
            pass


def login(page, cfg):
    """LOGIN_URL を開き、ユーザー名・パスワードを入力してログインする。

    ログイン設定(URL・ユーザー・パスワード)が揃っていない場合は何もしない。
    ユーザー名の後にパスワード欄が出てくる「2段階ログイン」にも対応する。
    成功可否は LOGIN_SUCCESS_SELECTOR が指定されていればそれで確認する。
    """
    if not cfg["use_login"]:
        return

    print(f"[ログイン] {cfg['login_url']}")
    page.goto(cfg["login_url"], wait_until="networkidle", timeout=60000)
    _perform_login_on_page(page, cfg)
    _verify_login(page, cfg)
    print("  ✓ ログインに成功しました\n")


def open_quiz_page(page, cfg):
    """問題ページを開く。ログイン画面が出たらその場でログインして問題ページに入る。

    - セッションが引き継がれない(SPAでメモリ内トークンが消える)サイト
    - 保護ページに直接アクセスすると returnUrl 付きログインへ飛ばされるサイト
    のどちらでも、問題ページにたどり着けるようにする。
    """
    print(f"[開く] {cfg['url']}")
    page.goto(cfg["url"], wait_until="networkidle", timeout=60000)
    install_cursor(page)

    # ログインが不要、またはログイン画面が出ていないならそのまま
    if not (cfg["use_login"] and _login_form_present(page, cfg)):
        return

    print("[再ログイン] 問題ページでログイン画面が出たため、その場でログインします")
    print("            (最初のログインのセッションが引き継がれていないようです)")
    _perform_login_on_page(page, cfg)
    try:
        page.wait_for_load_state("networkidle", timeout=20000)
    except PWTimeoutError:
        pass
    time.sleep(1.0)

    # returnUrl で自動的に問題ページへ戻らない場合は、もう一度問題URLを開く
    if _login_form_present(page, cfg) or page.locator(cfg["form_selector"]).count() == 0:
        page.goto(cfg["url"], wait_until="networkidle", timeout=60000)
        install_cursor(page)

    # それでもログイン画面のままなら失敗
    if _login_form_present(page, cfg):
        raise RuntimeError(
            "ログインしても問題ページに入れませんでした(セッションが保持されていない可能性があります)"
        )
    print("  ✓ 問題ページに入れました\n")


# ------------------------------------------------------------
# 「回答」ボタン・「次へ」ボタンを探す
# ------------------------------------------------------------
ANSWER_BUTTON_TEXTS = ["回答する", "解答する", "回答", "解答", "答える", "決定", "送信する"]
NEXT_BUTTON_TEXTS = ["次の問題", "次へ進む", "次に進む", "次へ", "つぎへ", "進む"]
# これらの語を含むボタンは「中断/離脱」用なので絶対に押さない
ABORT_WORDS = ["中断", "中止", "やめ", "終了", "ログアウト", "戻る", "破棄", "削除", "リセット"]
# 「中断しますか?」モーダルを閉じる(＝続行する)ボタンの文言
CANCEL_TEXTS = ["キャンセル", "いいえ", "閉じる", "続ける", "続行"]
# 「中断しますか?」モーダルが出ていると判定するための特徴的な文言
ABORT_MODAL_HINTS = ["中断しますか", "解答を中断", "中断してもよろしい", "中断します"]
# クリック対象になりうる要素(テキスト検索の対象を絞る)
_CLICKABLE = "button, a, [role=button], input[type=submit], input[type=button]"


def _loc_visible(loc):
    """ロケータが1つ以上あり、先頭が表示されているか(例外は握りつぶす)。"""
    try:
        return loc.count() > 0 and loc.first.is_visible()
    except Exception:
        return False


def _element_text(el):
    """要素の表示テキスト + value属性(input用)をまとめて返す。"""
    txt = ""
    try:
        txt = el.inner_text() or ""
    except Exception:
        pass
    if not txt:
        try:
            txt = el.get_attribute("value") or ""
        except Exception:
            pass
    return txt


def _first_safe(loc):
    """表示中で、かつ「中断/離脱」系の文言を含まない最初の要素を返す。"""
    try:
        n = loc.count()
    except Exception:
        return None
    for i in range(min(n, 15)):
        el = loc.nth(i)
        try:
            if not el.is_visible():
                continue
        except Exception:
            continue
        text = _element_text(el)
        if any(w in text for w in ABORT_WORDS):
            continue  # 「解答を中断する」等はスキップ
        return el
    return None


def _find_button(page, selector, texts):
    """selector優先。無ければ文言(texts)からボタンを探す。

    「中断」「戻る」等を含むボタンは押さない。完全一致を優先し、無ければ部分一致。
    """
    # 1) 明示セレクタ(指定されたものは信頼してそのまま使う)
    if selector:
        loc = page.locator(selector)
        if _loc_visible(loc):
            return loc.first
    # 2) まず完全一致(アクセシブル名)で探す
    for t in texts:
        try:
            cand = _first_safe(page.get_by_role("button", name=t, exact=True))
            if cand is not None:
                return cand
        except Exception:
            pass
    # 3) 次に部分一致(クリック可能要素に限定 + 中断系は除外)
    for t in texts:
        cand = _first_safe(page.locator(_CLICKABLE).filter(has_text=t))
        if cand is not None:
            return cand
        cand = _first_safe(
            page.locator(
                f'input[type=submit][value*="{t}"], input[type=button][value*="{t}"]'
            )
        )
        if cand is not None:
            return cand
    return None


def find_answer_button(page, cfg):
    """選択後に押す「回答/次へ」ボタンを探す。

    明示指定(ANSWER_BUTTON_SELECTOR)が最優先。無ければ回答系→次へ系の順で探す。
    「解答を中断する」等の中断ボタンは押さない。
    """
    if cfg.get("answer_button_selector"):
        btn = _find_button(page, cfg["answer_button_selector"], [])
        if btn is not None:
            return btn
    return _find_button(page, "", ANSWER_BUTTON_TEXTS + NEXT_BUTTON_TEXTS)


def dismiss_abort_modal(page):
    """「解答を中断しますか?」等のモーダルが出ていたら「キャンセル」を押して閉じる。

    ツールが誤って中断モーダルを出してしまった場合の保険。閉じられたら True。
    """
    try:
        body = page.locator("body").inner_text(timeout=800) or ""
    except Exception:
        return False
    if not any(h in body for h in ABORT_MODAL_HINTS):
        return False
    for t in CANCEL_TEXTS:
        loc = page.locator(_CLICKABLE).filter(has_text=t)
        if _loc_visible(loc):
            print(f"  [中断モーダル] 「{t}」を押して閉じます(中断しません)")
            move_and_click(page, loc.first)
            time.sleep(0.4)
            return True
    return False


def try_click_next(page, cfg):
    """フィードバック画面などで出る「次へ」ボタンを押す。

    誤クリック防止のため、次のどちらかのときだけ動く:
      - NEXT_SELECTOR が指定されている(そのセレクタだけを押す)
      - AUTO_NEXT=true のとき(文言から「次へ」系を自動で探す)
    どちらでもなければ何もしない(回答ボタンだけで次に進むサイト向け)。
    """
    if cfg.get("next_selector"):
        btn = _find_button(page, cfg["next_selector"], [])
    elif cfg.get("auto_next"):
        btn = _find_button(page, "", NEXT_BUTTON_TEXTS)
    else:
        return False
    if btn is not None:
        print("  「次へ」ボタンを押します")
        move_and_click(page, btn)
        return True
    return False


def question_signature(page, cfg):
    """今表示中の問題を識別する署名(URL + 設問テキストの先頭)。

    これが変われば「次の問題に切り替わった」とみなす。
    """
    parts = [page.url]
    loc = page.locator(cfg["question_selector"])
    try:
        if loc.count() > 0:
            parts.append((loc.first.inner_text() or "").strip()[:300])
    except Exception:
        pass
    return "||".join(parts)


def wait_for_next_question(page, cfg, old_sig):
    """回答後、問題が切り替わる(署名が変わる)のを待つ。

    フィードバック画面で止まっている場合は「次へ」ボタンを一度押して先に進める。
    切り替われば True、時間内に変わらなければ False。
    """
    deadline = time.time() + cfg["question_timeout"] / 1000.0
    tried_next = False
    while time.time() < deadline:
        time.sleep(0.5)
        # 万一「中断しますか?」モーダルが出ていたらキャンセルして続行
        dismiss_abort_modal(page)
        if question_signature(page, cfg) != old_sig:
            return True
        if not tried_next and try_click_next(page, cfg):
            tried_next = True
            time.sleep(0.5)
    return False


# ------------------------------------------------------------
# 全問を「解く→選択→回答→次へ」で回す
# ------------------------------------------------------------
def solve_all_questions(page, client, cfg):
    results = []
    for qi in range(1, cfg["max_questions"] + 1):
        # 現在の問題の解答欄が表示されるのを待つ
        try:
            page.wait_for_selector(
                cfg["form_selector"], state="visible", timeout=cfg["question_timeout"]
            )
        except PWTimeoutError:
            if qi == 1:
                print(f"[エラー] 解答欄({cfg['form_selector']})が見つかりませんでした。")
                if not cfg["auto_assignments"]:
                    print("        このURLがホーム画面など「問題ページではない」場合は、")
                    print("        .env で AUTO_ASSIGNMENTS=true にして課題を自動巡回してください。")
                print("        (問題ページを直接開く場合は QUESTION_SELECTOR/FORM_SELECTOR も確認)")
            else:
                print("これ以上、解答欄が見つからないため終了します(全問終了とみなします)。")
            break

        print(f"===== 設問 {qi} =====")
        form = page.locator(cfg["form_selector"]).first
        sig = question_signature(page, cfg)

        options = extract_options(form)
        if not options:
            print("  選択肢が取得できませんでした。終了します。")
            break

        q_text = nearest_question_text(form, cfg["question_selector"])
        shot_target = page.locator(cfg["question_selector"])
        if shot_target.count() == 0:
            shot_target = form  # 見つからなければフォームだけ
        img_bytes = shot_target.first.screenshot()

        # Gemini に解かせる
        print("  Geminiに問い合わせ中...")
        try:
            answer, reason, raw = ask_gemini(client, cfg["model"], img_bytes, q_text, options)
        except Exception as e:  # noqa: BLE001
            print(f"  [中断] Geminiへの問い合わせに失敗しました: {e}")
            print("         時間をおいて再実行するか、.env の GEMINI_MODEL を")
            print("         別のモデル(例: gemini-2.0-flash)に変えて試してください。")
            break

        # 記号 → 対応するラジオを決める
        target = None
        if answer:
            print(f"  → Geminiの解答: 【{answer}】  根拠: {reason or '(なし)'}")
            for o in options:
                if (o["symbol"] or SYMBOLS[o["index"]]) == answer:
                    target = o
                    break
        if target is None:
            # 判定不能/記号不一致でも、どんどん進めるため先頭を仮選択する
            target = options[0]
            shown = answer or "不明"
            fallback_sym = target["symbol"] or SYMBOLS[target["index"]]
            print(f"  [注意] 解答を確定できませんでした(Gemini: {shown})。仮に「{fallback_sym}」を選びます。")
            results.append((qi, f"{shown}(仮選択)"))
        else:
            results.append((qi, answer))

        # ラジオを選択
        label = target["input"].locator("xpath=ancestor::label[1]")
        click_target = label if label.count() > 0 else target["input"]
        move_and_click(page, click_target.first)
        sym = target["symbol"] or SYMBOLS[target["index"]]
        print(f"  ✓ 「{sym}」(value={target['value']}) を選択しました")
        time.sleep(0.3)

        # 「回答」ボタンを押して次の問題へ
        btn = find_answer_button(page, cfg)
        if btn is None:
            print("  [終了] 回答ボタンが見つかりませんでした(最後の問題だった可能性があります)。")
            print("         もし回答ボタンがあるのに押せない場合は .env の ANSWER_BUTTON_SELECTOR を設定してください。")
            break
        print("  回答ボタンを押します")
        move_and_click(page, btn)
        # 万一「中断しますか?」モーダルが出たらキャンセルして続行(中断しない)
        dismiss_abort_modal(page)

        # 次の問題に切り替わるのを待つ(必要なら「次へ」も押す)
        if not wait_for_next_question(page, cfg, sig):
            print("  [終了] 次の問題に切り替わりませんでした。全問終了とみなします。\n")
            break
        print()
    return results


# ------------------------------------------------------------
# 課題を自動で巡回する(ホーム→学習トレーニング→課題→…)
# ------------------------------------------------------------
def click_text(page, text, timeout_ms=15000, optional=False):
    """画面上の指定文言のボタン/リンクをクリックする。

    クリック可能な要素(button/a等)を優先。見つからなければ、その文言を持つ
    表示中の要素を直接クリックする。optional=Falseで見つからなければ例外。
    """
    if not text:
        return False
    deadline = time.time() + timeout_ms / 1000.0
    while time.time() < deadline:
        btn = _find_button(page, "", [text])
        if btn is not None:
            print(f"  「{text}」をクリックします")
            move_and_click(page, btn)
            return True
        try:
            loc = page.get_by_text(text, exact=False)
            if _loc_visible(loc):
                print(f"  「{text}」をクリックします")
                move_and_click(page, loc.first)
                return True
        except Exception:
            pass
        time.sleep(0.4)
    if optional:
        return False
    raise RuntimeError(f"「{text}」が見つかりませんでした")


def _assignment_locators(page, cfg):
    """課題一覧の各項目のロケータ(複数)を返す。"""
    if cfg["assignment_selector"]:
        return page.locator(cfg["assignment_selector"])
    # セレクタ未指定: 「未完了」等を含むクリック可能なカードを推定
    inc = cfg["incomplete_text"]
    xp = (
        f"xpath=//a[contains(normalize-space(.),'{inc}')] "
        f"| //*[@onclick][contains(normalize-space(.),'{inc}')] "
        f"| //*[contains(@class,'assignment') or contains(@class,'card') "
        f"or contains(@class,'task') or contains(@class,'kadai')]"
        f"[contains(normalize-space(.),'{inc}')]"
    )
    return page.locator(xp)


def _assignment_list_present(page, cfg):
    """今、課題一覧の画面にいるか(課題項目が見えているか)。"""
    if cfg["assignment_selector"]:
        return _loc_visible(page.locator(cfg["assignment_selector"]))
    try:
        body = page.locator("body").inner_text(timeout=800) or ""
    except Exception:
        return False
    return cfg["incomplete_text"] in body


def find_next_incomplete_assignment(page, cfg):
    """未完了の課題を上から探して (要素, タイトル文字列) で返す。無ければ (None, None)。"""
    inc = cfg["incomplete_text"]
    loc = _assignment_locators(page, cfg)
    try:
        n = loc.count()
    except Exception:
        n = 0
    for i in range(min(n, 100)):
        el = loc.nth(i)
        try:
            if not el.is_visible():
                continue
            txt = el.inner_text() or ""
        except Exception:
            continue
        if inc in txt:
            return el, txt.replace("\n", " ").strip()
    return None, None


def go_to_assignment_list(page, cfg):
    """(再)課題一覧まで移動する。すでに一覧にいれば何もしない。"""
    if _assignment_list_present(page, cfg):
        return True
    # 保存済みの一覧URLがあれば開く(複数ページ型サイト向け)
    if cfg.get("_list_url"):
        try:
            page.goto(cfg["_list_url"], wait_until="networkidle", timeout=60000)
            install_cursor(page)
            if _assignment_list_present(page, cfg):
                return True
        except Exception:
            pass
    # クリックでたどる: 学習トレーニング → 課題
    click_text(page, cfg["training_text"], optional=True)
    time.sleep(0.5)
    click_text(page, cfg["assignment_menu_text"], optional=True)
    time.sleep(0.5)
    if _assignment_list_present(page, cfg):
        cfg["_list_url"] = page.url
        return True
    return False


def run_assignments(page, client, cfg):
    """課題一覧から未完了の課題を順に開いて解き、次の課題へ進むのを繰り返す。"""
    print("[自動巡回] 課題を上から順に解いていきます\n")
    if not go_to_assignment_list(page, cfg):
        print("[エラー] 課題一覧にたどり着けませんでした。")
        print("        .env の TRAINING_TEXT / ASSIGNMENT_MENU_TEXT / ASSIGNMENT_SELECTOR を確認してください。")
        return []

    all_results = []
    seen = set()
    for a in range(1, cfg["max_assignments"] + 1):
        if not go_to_assignment_list(page, cfg):
            print("課題一覧に戻れませんでした。終了します。")
            break

        el, title = find_next_incomplete_assignment(page, cfg)
        if el is None:
            print("\n未完了の課題が見つかりませんでした。全課題を処理したとみなして終了します。")
            break

        key = (title or "")[:80]
        if key in seen:
            print(f"\n課題「{key}」が完了扱いにならず繰り返しています。ここで終了します。")
            break
        seen.add(key)

        print(f"\n########## 課題 {a}: {key} ##########")
        move_and_click(page, el)
        time.sleep(0.6)

        # 「開始する」を押す
        if not click_text(page, cfg["start_text"], optional=True):
            print("  [注意] 「開始する」が見つかりませんでした。次の課題へ移ります。")
            continue
        time.sleep(0.6)

        # 解答欄が出るまで待つ
        try:
            page.wait_for_selector(
                cfg["form_selector"], state="visible", timeout=cfg["question_timeout"]
            )
        except PWTimeoutError:
            print("  [注意] 問題が表示されませんでした。次の課題へ移ります。")
            continue

        # 全問を解く
        results = solve_all_questions(page, client, cfg)
        all_results.append((key, results))

        # 「答え合わせへ」→「課題詳細画面へ」を押して一覧へ戻る
        dismiss_abort_modal(page)
        click_text(page, cfg["grading_text"], optional=True)
        time.sleep(0.6)
        dismiss_abort_modal(page)
        click_text(page, cfg["back_to_list_text"], optional=True)
        time.sleep(0.6)
        dismiss_abort_modal(page)

    # サマリ
    print("\n========== 全課題の結果 ==========")
    print(f"  処理した課題数: {len(all_results)}")
    for title, results in all_results:
        print(f"  ● {title}  ({len(results)}問)")
        for num, ans in results:
            print(f"      設問{num}: {ans}")
    print("==================================")
    return all_results


# ------------------------------------------------------------
# メイン
# ------------------------------------------------------------
def run(cfg):
    client = genai.Client(api_key=cfg["api_key"])

    # 起動時の設定を分かりやすく表示(どのモードで何を開くか)
    print("=" * 56)
    if cfg["auto_assignments"]:
        print("  モード: 課題を自動で巡回して全部解く (AUTO_ASSIGNMENTS=true)")
        print(f"  最初に開くホーム画面: {cfg['url']}")
    else:
        print("  モード: 1つの問題ページだけを解く (AUTO_ASSIGNMENTS=false)")
        print(f"  開く問題ページ: {cfg['url']}")
        print("  ※課題を自動巡回したい場合は .env で AUTO_ASSIGNMENTS=true にしてください")
    print(f"  ログイン: {'あり' if cfg['use_login'] else 'なし'}")
    print("=" * 56 + "\n")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=cfg["headless"], slow_mo=cfg["slow_mo"])
        page = browser.new_page(viewport={"width": 1280, "height": 900})

        # 確認ダイアログ(「中断しますか?」等)で止まらないよう自動処理を仕込む
        install_dialog_handler(page, cfg)

        # 先にログイン(設定があるときだけ実行)。同じブラウザなのでセッションは引き継がれる
        try:
            login(page, cfg)
        except Exception as e:
            print(f"[エラー] ログインに失敗したため中止します: {e}")
            browser.close()
            return

        # 問題ページを開く。ログイン画面に飛ばされたらその場でログインし直す
        try:
            open_quiz_page(page, cfg)
        except Exception as e:
            print(f"[エラー] 問題ページを開けませんでした: {e}")
            browser.close()
            return

        if cfg["auto_assignments"]:
            # 課題一覧から未完了の課題を順に開いて、全部解く
            run_assignments(page, client, cfg)
        else:
            # 単一の問題ページを「解く→選択→回答→次へ」で処理する
            results = solve_all_questions(page, client, cfg)

            # 最後に採点/送信ボタン(設定されていて、表示されていれば)
            if cfg["submit_selector"]:
                btn = page.locator(cfg["submit_selector"])
                if _loc_visible(btn):
                    print(f"[送信] {cfg['submit_selector']} をクリックします")
                    move_and_click(page, btn.first)
                    time.sleep(1.5)

            # サマリ
            print("\n========== 結果一覧 ==========")
            print(f"  解いた問題数: {len(results)}")
            for num, ans in results:
                print(f"  設問{num}: {ans}")
            print("==============================")

        if not cfg["headless"]:
            print("\n確認できたら Enter キーでブラウザを閉じます...")
            try:
                input()
            except EOFError:
                time.sleep(5)
        browser.close()


if __name__ == "__main__":
    cfg = load_config()
    run(cfg)
