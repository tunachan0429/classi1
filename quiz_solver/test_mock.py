# -*- coding: utf-8 -*-
"""Geminiを使わずに、抽出とクリックのロジックを検証するテスト。"""
import pathlib
from playwright.sync_api import sync_playwright
import solve

MOCK = pathlib.Path(__file__).parent / "mock" / "quiz.html"
LOGIN_MOCK = pathlib.Path(__file__).parent / "mock" / "login.html"
LOGIN_2STEP_MOCK = pathlib.Path(__file__).parent / "mock" / "login_2step.html"
LOGIN_ENTER_MOCK = pathlib.Path(__file__).parent / "mock" / "login_enter.html"
QUIZ_GUARDED_MOCK = pathlib.Path(__file__).parent / "mock" / "quiz_guarded.html"
STUB_ANSWER = "イ"  # 本来Geminiが返す想定の記号(20人 = 3+7+6+4=20 が正解)


def make_login_cfg(user, password, url=None, next_selector=""):
    """login() に渡すための最小限の設定を組み立てる。"""
    return {
        "use_login": True,
        "login_url": (url or LOGIN_MOCK).resolve().as_uri(),
        "login_user": user,
        "login_pass": password,
        "login_user_selector": "input[name=username]",
        "login_pass_selector": "input[type=password]",
        "login_button_selector": "button[type=submit]",
        "login_next_selector": next_selector,
        "login_success_selector": "#dashboard",
    }


def test_login(browser):
    """正しい資格情報でログインでき、間違いは失敗として検出できるか。"""
    # 1) 正しい資格情報 → 成功
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    solve.login(page, make_login_cfg("testuser", "testpass"))
    assert page.locator("#dashboard").count() == 1, "ログイン後の目印が出ていない"
    page.close()
    print("ログイン成功ケース OK")

    # 2) 間違った資格情報 → 例外(検証失敗)
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    try:
        solve.login(page, make_login_cfg("wrong", "wrong"))
    except RuntimeError:
        print("ログイン失敗ケース OK (誤資格情報を正しく検出)")
    else:
        raise AssertionError("誤った資格情報なのにログイン成功と判定された")
    finally:
        page.close()

    # 3) ログイン設定なし → 何もせず素通り
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    solve.login(page, {"use_login": False})
    page.close()
    print("ログイン未設定ケース OK (スキップ)")

    # 4) 2段階ログイン(パスワード欄が最初は非表示) → 「次へ」を押して成功
    #    next_selector を指定せず、ログインボタン兼用でパスワード欄を出せることを確認
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    solve.login(page, make_login_cfg("testuser", "testpass", url=LOGIN_2STEP_MOCK))
    assert page.locator("#dashboard").count() == 1, "2段階ログイン後の目印が出ていない"
    page.close()
    print("2段階ログイン(自動検出)ケース OK")

    # 5) 2段階ログイン + LOGIN_NEXT_SELECTOR を明示指定 → 成功
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    solve.login(
        page,
        make_login_cfg("testuser", "testpass", url=LOGIN_2STEP_MOCK, next_selector="#submit-btn"),
    )
    assert page.locator("#dashboard").count() == 1, "2段階ログイン(明示next)後の目印が出ていない"
    page.close()
    print("2段階ログイン(next明示)ケース OK")

    # 6) 送信ボタンが無く Enter だけで進む/送信するサイト → 成功
    #    ログインボタンが見つからない場合に Enter フォールバックが働くことを確認
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    solve.login(page, make_login_cfg("testuser", "testpass", url=LOGIN_ENTER_MOCK))
    assert page.locator("#dashboard").count() == 1, "Enter送信ログイン後の目印が出ていない"
    page.close()
    print("Enter送信(ボタン無し)ケース OK")


def test_open_quiz_page(browser):
    """問題ページでログイン画面が出た場合に、その場でログインして問題に入れるか。

    ユーザーの報告「ログインしても問題URLに移るとまたログイン画面が出る」ケースを再現。
    """
    cfg = {
        "url": QUIZ_GUARDED_MOCK.resolve().as_uri(),
        "use_login": True,
        "login_user": "testuser",
        "login_pass": "testpass",
        "login_user_selector": "input[name=username]",
        "login_pass_selector": "input[type=password]",
        "login_button_selector": "button[type=submit]",
        "login_next_selector": "",
        "form_selector": "tui-single-select-form",
    }
    # 1) 問題ページで再ログイン → 問題フォームに入れる
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    solve.open_quiz_page(page, cfg)
    assert page.locator("tui-single-select-form").first.is_visible(), "問題フォームが表示されていない"
    assert not solve._login_form_present(page, cfg), "まだログイン画面が残っている"
    page.close()
    print("問題ページでの再ログイン → 問題表示 OK")

    # 2) 誤った資格情報 → 問題ページに入れず失敗
    bad = dict(cfg, login_pass="wrong")
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    try:
        solve.open_quiz_page(page, bad)
    except RuntimeError:
        print("誤資格情報での再ログイン失敗検出 OK")
    else:
        raise AssertionError("誤った資格情報なのに問題ページに入れたと判定された")
    finally:
        page.close()


def main():
    url = MOCK.resolve().as_uri()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        # 0) ログイン処理のテスト
        print("---- ログインテスト ----")
        test_login(browser)
        print()

        # 0b) 問題ページで再ログインするテスト
        print("---- 問題ページ再ログインテスト ----")
        test_open_quiz_page(browser)
        print()

        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.goto(url)
        solve.install_cursor(page)

        forms = page.locator("tui-single-select-form")
        assert forms.count() == 1, f"想定1件だが {forms.count()} 件"
        form = forms.nth(0)

        # 1) 選択肢抽出
        options = solve.extract_options(form)
        print("抽出した選択肢:")
        for o in options:
            print(f"   index={o['index']} symbol={o['symbol']!r} value={o['value']}")
        symbols = [o["symbol"] for o in options]
        assert symbols == ["ア", "イ", "ウ", "エ"], f"記号抽出失敗: {symbols}"
        assert [o["value"] for o in options] == [f"SINGLE_SELECT_{i}" for i in range(4)]

        # 2) 設問テキスト抽出
        q = solve.nearest_question_text(form, "tui-section-question")
        assert "累積度数" in q, f"設問テキスト抽出失敗: {q!r}"
        print("\n設問テキスト(先頭50字):", q[:50].replace("\n", " "))

        # 3) スクショが撮れるか
        shot = page.locator("tui-section-question").first.screenshot()
        assert len(shot) > 1000, "スクショが空"
        print(f"\nスクショOK: {len(shot)} bytes")

        # 4) parse_answer のJSON解釈
        a, r, _ = solve.parse_answer('{"answer":"イ","reason":"3+7+6+4=20"}', symbols)
        assert a == "イ" and "20" in r, f"parse失敗: {a} / {r}"
        # 4b) JSONが壊れていても記号を拾えるか
        a2, _, _ = solve.parse_answer("答えはウだと思います", symbols)
        assert a2 == "ウ", f"fallback失敗: {a2}"
        print("parse_answer OK (JSON + フォールバック両方)")

        # 5) カーソル移動+クリックで実際に選択されるか
        target = next(o for o in options if o["symbol"] == STUB_ANSWER)
        label = target["input"].locator("xpath=ancestor::label[1]")
        solve.move_and_click(page, label.first)
        checked = target["input"].is_checked()
        assert checked, "ラジオが選択されていない"
        print(f"\nクリック検証OK: 「{STUB_ANSWER}」(value={target['value']}) が選択された = {checked}")

        # 他が選択されていないことも確認
        others = [o for o in options if o["symbol"] != STUB_ANSWER]
        assert all(not o["input"].is_checked() for o in others), "他の選択肢まで選ばれている"

        browser.close()
    print("\n==== 全テスト成功 ====")


if __name__ == "__main__":
    main()
