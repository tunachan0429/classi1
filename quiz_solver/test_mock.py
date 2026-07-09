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
QUIZ_MULTI_MOCK = pathlib.Path(__file__).parent / "mock" / "quiz_multi.html"
DIALOG_MOCK = pathlib.Path(__file__).parent / "mock" / "dialog.html"
ABORT_BUTTONS_MOCK = pathlib.Path(__file__).parent / "mock" / "abort_buttons.html"
FULL_FLOW_MOCK = pathlib.Path(__file__).parent / "mock" / "full_flow.html"
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


def test_solve_all_questions(browser):
    """1問ずつ「選択→回答ボタン→次の問題」で全問を回せるか(Geminiはスタブ)。"""
    cfg = {
        "url": QUIZ_MULTI_MOCK.resolve().as_uri(),
        "form_selector": "tui-single-select-form",
        "question_selector": "tui-section-question",
        "answer_button_selector": "",   # 文言「回答」から自動検出させる
        "next_selector": "",
        "max_questions": 50,
        "question_timeout": 5000,
        "model": "dummy",
    }
    # Gemini を呼ばずに、常に「ア」を返すスタブに差し替える
    original = solve.ask_gemini
    solve.ask_gemini = lambda client, model, img, qtext, options: ("ア", "テスト用", "ア")
    try:
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.goto(cfg["url"])
        results = solve.solve_all_questions(page, None, cfg)
        assert len(results) == 3, f"3問すべて解く想定だが {len(results)} 問だった: {results}"
        assert page.locator("#done").is_visible(), "完了表示が出ていない"
        # 各問「ア」を選んで回答できたこと
        assert all(ans == "ア" for _, ans in results), f"回答内容が想定と違う: {results}"
        page.close()
    finally:
        solve.ask_gemini = original
    print(f"連続回答(選択→回答→次へ)テスト OK: {len(results)}問を処理")


def test_dialog_handler(browser):
    """「中断しますか?」などの確認ダイアログを自動処理できるか。"""
    url = DIALOG_MOCK.resolve().as_uri()

    # accept: OKを押す → 次に進む(#after が表示される)
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    solve.install_dialog_handler(page, {"dialog_action": "accept"})
    page.goto(url)
    page.click("#go")
    assert page.locator("#after").is_visible(), "acceptなのに次へ進んでいない"
    page.close()
    print("ダイアログ accept ケース OK (自動でOKを押して進めた)")

    # dismiss: キャンセルを押す → 進まない(#after は非表示のまま)
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    solve.install_dialog_handler(page, {"dialog_action": "dismiss"})
    page.goto(url)
    page.click("#go")
    assert not page.locator("#after").is_visible(), "dismissなのに進んでしまった"
    page.close()
    print("ダイアログ dismiss ケース OK (自動でキャンセルを押した)")


def test_abort_button_avoidance(browser):
    """「解答を中断する」ボタンを避けて「次へ」を押せるか + 中断モーダルを閉じられるか。"""
    url = ABORT_BUTTONS_MOCK.resolve().as_uri()

    # 1) find_answer_button は「中断」ではなく「次へ」を選ぶ
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    page.goto(url)
    btn = solve.find_answer_button(page, {"answer_button_selector": ""})
    assert btn is not None, "ボタンが見つからない"
    btn.click()
    assert page.evaluate("() => window.__nextClicked === true"), "「次へ」が押されていない"
    assert not page.evaluate("() => window.__aborted === true"), "「中断」を押してしまった"
    # 中断モーダルは出ていないはず
    assert not page.locator("#modal").is_visible(), "中断モーダルが出てしまった"
    page.close()
    print("中断ボタン回避 OK (「次へ」を正しく選択)")

    # 2) 中断モーダルが出ている場合は dismiss_abort_modal がキャンセルを押して閉じる
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    page.goto(url)
    page.click("#abort")  # わざと中断モーダルを出す
    assert page.locator("#modal").is_visible(), "前提: モーダルが出ている"
    closed = solve.dismiss_abort_modal(page)
    assert closed, "モーダルを閉じられなかった"
    assert page.evaluate("() => window.__cancelled === true"), "キャンセルが押されていない"
    assert not page.locator("#modal").is_visible(), "モーダルが閉じていない"
    page.close()
    print("中断モーダルのキャンセル OK")


def test_full_flow(browser):
    """ホーム→学習トレーニング→課題→課題選択→開始→解答→答え合わせ→次の課題 を巡回できるか。"""
    cfg = {
        "url": FULL_FLOW_MOCK.resolve().as_uri(),
        "form_selector": "tui-single-select-form",
        "question_selector": "tui-section-question",
        "answer_button_selector": "",
        "next_selector": "",
        "auto_next": False,
        "max_questions": 50,
        "question_timeout": 4000,
        "model": "dummy",
        # 巡回用
        "training_text": "学習トレーニング",
        "assignment_menu_text": "課題",
        "start_text": "開始する",
        "grading_text": "答え合わせ",
        "back_to_list_text": "課題詳細画面へ",
        "assignment_selector": ".assignment",
        "incomplete_text": "未完了",
        "max_assignments": 10,
        "max_steps": 20,
    }
    original = solve.ask_gemini
    solve.ask_gemini = lambda client, model, img, qtext, options: ("ア", "テスト用", "ア")
    try:
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        # sessionStorage をまっさらにするため一度開いてクリア
        page.goto(cfg["url"])
        page.evaluate("() => sessionStorage.clear()")
        page.reload()
        all_results = solve.run_assignments(page, None, cfg)
        # 課題A(テスト2問 + おすすめ演習1問 = 3問), 課題B(テスト2問)
        assert len(all_results) == 2, f"2課題を処理する想定だが {len(all_results)} 件: {all_results}"
        counts = [len(r) for _, r in all_results]
        assert counts == [3, 2], f"各課題の問題数が想定と違う(課題A=3, 課題B=2 のはず): {counts}"
        page.close()
    finally:
        solve.ask_gemini = original
    print(f"全自動フロー(マルチステップ) OK: {len(all_results)}課題, 各問題数={counts}")


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

        # 0c) 連続回答(選択→回答→次へ)のテスト
        print("---- 連続回答テスト ----")
        test_solve_all_questions(browser)
        print()

        # 0d) 確認ダイアログ自動処理のテスト
        print("---- 確認ダイアログテスト ----")
        test_dialog_handler(browser)
        print()

        # 0e) 中断ボタン回避・中断モーダルのテスト
        print("---- 中断ボタン回避テスト ----")
        test_abort_button_avoidance(browser)
        print()

        # 0f) 全自動フロー(課題巡回)のテスト
        print("---- 全自動フローテスト ----")
        test_full_flow(browser)
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
