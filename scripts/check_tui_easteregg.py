"""Headless Textual pilot checks for the hidden ↑↑↓↓←→←→baba picture dialog. Fake client only."""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from chatlocal.tui import easter_egg  # noqa: E402
from chatlocal.tui.app import TulpaApp  # noqa: E402
from chatlocal.tui.easter_egg import EasterEggModal, SequenceTracker  # noqa: E402
from check_terminal_tui import FakeClient, settle  # noqa: E402
from textual.widgets import Input  # noqa: E402

ARROWS = ['up', 'up', 'down', 'down', 'left', 'right', 'left', 'right']
CODE = ARROWS + ['b', 'a', 'b', 'a']
PREVIEW = ROOT / '.tmp' / 'easter-egg-preview.svg'


def check_tracker():
    t = SequenceTracker()
    assert [t.feed(k) for k in CODE][-1] == (True, True)
    assert t.progress == 0
    # Overlap: ↑↑↑ keeps the last two ups as a prefix.
    t.reset()
    for k in ['up', 'up', 'up', 'down', 'down', 'left', 'right', 'left', 'right', 'b', 'a', 'b']:
        assert t.feed(k)[1] is False
    assert t.feed('a') == (True, True), 'overlapping prefix must still complete'
    # Wrong key restarts; arrows are never consumed; b/a only after 8 arrows.
    t.reset()
    assert all(t.feed(k) == (False, False) for k in ARROWS)
    assert t.feed('b') == (True, False) and t.feed('x') == (False, False) and t.progress == 0
    assert t.feed('b') == (False, False) and t.feed('a') == (False, False), 'loose b/a must reach the page'
    # A mistyped b/a after the arrows must not fall through to a page action.
    t.reset()
    for k in ARROWS + ['b']:
        t.feed(k)
    assert t.feed('b') == (True, False) and t.progress == 0


def egg_open(app):
    return isinstance(app.screen, EasterEggModal)


async def press(app, pilot, keys):
    for key in keys:
        await pilot.press(key)
    await settle(app, pilot, rounds=2)


async def check_app():
    assert easter_egg.ImageWidget is not None, easter_egg.IMAGE_ERROR
    assert easter_egg.IMAGE_DIR == ROOT / 'web' / 'easter-egg'
    client = FakeClient()
    app = TulpaApp(client=client)
    async with app.run_test(size=(120, 40)) as pilot:
        await settle(app, pilot)
        # Wrong sequence: nothing opens.
        await press(app, pilot, ARROWS + ['b', 'b', 'a'])
        assert not egg_open(app)
        await press(app, pilot, ['1'])  # Back to the overview, sidebar focused.
        app.query_one('#sidebar').focus()
        await settle(app, pilot)
        focused = app.focused
        await press(app, pilot, CODE)
        assert egg_open(app), 'full sequence must open the dialog'
        await press(app, pilot, ['q', 'r', '3', 'escape'])
        assert not egg_open(app) and app.is_running and app.current == 'overview', 'q/r/digits blocked'
        assert app.focused is focused, 'focus restored after Esc'
        # Reopen, close via the button.
        await press(app, pilot, CODE)
        assert egg_open(app)
        await pilot.click('#egg-close')
        await settle(app, pilot, rounds=2)
        assert not egg_open(app)
        # Overlapping prefix in the real app.
        await press(app, pilot, ['up'] + CODE)
        assert egg_open(app), 'overlap must open'
        await press(app, pilot, ['escape'])

        # Mouse click in the middle resets.
        await press(app, pilot, ARROWS + ['b'])
        await pilot.click('#header')
        await press(app, pilot, ['a', 'b', 'a'])
        assert not egg_open(app), 'a click must reset the sequence'

        # Typing into an Input never triggers.
        await press(app, pilot, ['2'])
        field = app.query_one('#sl-http', Input)
        field.focus()
        await settle(app, pilot)
        await press(app, pilot, CODE)
        assert not egg_open(app) and field.value.endswith('baba'), 'Input must receive b/a'

        # b/a after the arrows must not run SL 托管's `b` (login); a lone b still does (control).
        logins = []
        page = app.page('managed')
        page.action_login = lambda: logins.append(1)
        app.action_goto('managed')
        await settle(app, pilot)
        await press(app, pilot, ['b'])
        assert logins == [1], 'control: a lone b reaches the page'
        await press(app, pilot, ARROWS + ['b', 'b'])
        assert not egg_open(app) and logins == [1], 'mistyped secret must not run login'
        await press(app, pilot, CODE)
        assert egg_open(app) and app.current == 'managed', 'opens on the managed page too'
        assert logins == [1], 'b inside the sequence must be consumed'
        await press(app, pilot, ['escape'])
        assert not egg_open(app)

        # Another dialog on top: no trigger.
        from chatlocal.tui.modals import ConfirmModal
        app.push_screen(ConfirmModal('t', 'b'))
        await settle(app, pilot)
        await press(app, pilot, ARROWS + ['b', 'a'])
        assert not egg_open(app)
        await press(app, pilot, ['escape'])
    print('app ok')


async def check_layout(size, save=False):
    app = TulpaApp(client=FakeClient())
    async with app.run_test(size=size) as pilot:
        await settle(app, pilot)
        await press(app, pilot, CODE)
        assert egg_open(app)
        await settle(app, pilot, rounds=3)
        left, right = app.screen.query_one('#egg-left'), app.screen.query_one('#egg-right')
        lr, rr = left.region, right.region
        assert lr.width > 0 and lr.height > 0, f'{size}: empty picture {lr}'
        assert lr.size == rr.size and lr.y == rr.y, f'{size}: unequal {lr} {rr}'
        assert lr.right <= rr.x, f'{size}: 1 must be left of 2: {lr} {rr}'
        assert rr.right <= size[0] and lr.bottom <= size[1], f'{size}: off screen {lr} {rr}'
        box = app.screen.query_one('#egg-close').region
        assert box.bottom <= size[1] and box.width > 0, f'{size}: close button hidden {box}'
        # Aspect ratio 1086:1448 with 10x20 px cells: cols/rows ≈ 1.5.
        aspect = lr.width * 10 / (lr.height * 20)
        assert abs(aspect - 1086 / 1448) < 0.15, f'{size}: aspect {aspect:.2f}'
        if save:
            PREVIEW.parent.mkdir(exist_ok=True)
            app.save_screenshot(str(PREVIEW))
        print(f'layout {size}: left={lr} right={rr}')


def main():
    check_tracker()
    asyncio.run(check_app())
    asyncio.run(check_layout((120, 40), save=True))
    asyncio.run(check_layout((80, 24)))
    asyncio.run(check_layout((60, 20)))
    print('easter egg ok')


if __name__ == '__main__':
    main()
