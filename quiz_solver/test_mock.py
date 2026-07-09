# -*- coding: utf-8 -*-
"""Geminiを使わずに、抽出とクリックのロジックを検証するテスト。"""
import pathlib
from playwright.sync_api import sync_playwright
import solve

MOCK = pathlib.Path(__file__).parent / "mock" / "quiz.html"
STUB_ANSWER = "イ"  # 本来Geminiが返す想定の記号(20人 = 3+7+6+4=20 が正解)


def main():
    url = MOCK.resolve().as_uri()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
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
