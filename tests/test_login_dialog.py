import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt6.QtWidgets import QApplication

from app.ui.login_dialog import LoginDialog


def _app() -> QApplication:
    instance = QApplication.instance()
    if instance is not None:
        return instance
    return QApplication([])


def test_work_order_typing_does_not_advance_focus():
    app = _app()
    dialog = LoginDialog()
    dialog.show()
    app.processEvents()

    dialog.shop_order_input.setFocus()
    app.processEvents()

    dialog.shop_order_input.setText('WO123456')
    app.processEvents()

    assert app.focusWidget() is dialog.shop_order_input

    dialog.validation_timer.stop()
    dialog.close()


def test_work_order_enter_advances_to_part_id(monkeypatch):
    app = _app()
    dialog = LoginDialog()
    dialog.show()
    app.processEvents()

    monkeypatch.setattr(dialog, '_validate_shop_order', lambda: None)
    dialog.shop_order_input.setFocus()
    dialog.shop_order_input.setText('WO123456')
    app.processEvents()

    dialog._on_shop_order_enter()
    app.processEvents()

    assert app.focusWidget() is dialog.part_id_input

    dialog.validation_timer.stop()
    dialog.close()
