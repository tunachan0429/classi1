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
        # 回答後にフィードバック画面などで出る「次へ」ボタン(空なら文言から自動検出)
        "next_selector": os.getenv("NEXT_SELECTOR", "").strip(),
        # 解く問題数の上限(暴走防止のセーフティ)
        "max_questions": args.max or int(os.getenv("MAX_QUESTIONS", "200")),
        # 次の問題を待つ/解答欄を待つときのタイムアウト(ミリ秒)
        "question_timeout": int(os.getenv("QUESTION_TIMEOUT_MS", "20000")),
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

    # 必須項目チェック
    problems = []
    if not cfg["api_key"] or cfg["api_key"].startswith("ここに"):
        problems.append("GEMINI_API_KEY が未設定です (.env を確認してください)")
    if not cfg["url"] or "example.com" in cfg["url"]:
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
    resp = client.models.generate_content(model=model, contents=contents)
    text = (resp.text or "").strip()
    return parse_answer(text, symbols)


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
NEXT_BUTTON_TEXTS = ["次の問題", "次へ進む", "次に進む", "次へ", "つぎへ", "続ける", "進む"]
# クリック対象になりうる要素(テキスト検索の対象を絞る)
_CLICKABLE = "button, a, [role=button], input[type=submit], input[type=button]"


def _loc_visible(loc):
    """ロケータが1つ以上あり、先頭が表示されているか(例外は握りつぶす)。"""
    try:
        return loc.count() > 0 and loc.first.is_visible()
    except Exception:
        return False


def _find_button(page, selector, texts):
    """selector優先で、無ければ文言(texts)からボタンを探し、表示中の先頭を返す。"""
    # 1) 明示セレクタ
    if selector:
        loc = page.locator(selector)
        if _loc_visible(loc):
            return loc.first
    # 2) 文言で探す(クリック可能な要素に限定)
    for t in texts:
        loc = page.locator(_CLICKABLE).filter(has_text=t)
        if _loc_visible(loc):
            return loc.first
        # input[type=submit/button] は value 属性に文言が入る
        loc = page.locator(
            f'input[type=submit][value*="{t}"], input[type=button][value*="{t}"]'
        )
        if _loc_visible(loc):
            return loc.first
        # aria-label など(アクセシブル名)でも探す
        try:
            loc = page.get_by_role("button", name=t)
            if _loc_visible(loc):
                return loc.first
        except Exception:
            pass
    return None


def find_answer_button(page, cfg):
    """1問ごとの「回答」ボタンを探す。"""
    return _find_button(page, cfg["answer_button_selector"], ANSWER_BUTTON_TEXTS)


def try_click_next(page, cfg):
    """フィードバック画面などで出る「次へ」ボタンがあれば押す。"""
    btn = _find_button(page, cfg["next_selector"], NEXT_BUTTON_TEXTS)
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
                print("        .env のセレクタ設定を確認してください。")
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
        answer, reason, raw = ask_gemini(client, cfg["model"], img_bytes, q_text, options)

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

        # 次の問題に切り替わるのを待つ(必要なら「次へ」も押す)
        if not wait_for_next_question(page, cfg, sig):
            print("  [終了] 次の問題に切り替わりませんでした。全問終了とみなします。\n")
            break
        print()
    return results


# ------------------------------------------------------------
# メイン
# ------------------------------------------------------------
def run(cfg):
    client = genai.Client(api_key=cfg["api_key"])

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=cfg["headless"], slow_mo=cfg["slow_mo"])
        page = browser.new_page(viewport={"width": 1280, "height": 900})

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

        # 全問を「解く→選択→回答→次へ」で順番に処理する
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
