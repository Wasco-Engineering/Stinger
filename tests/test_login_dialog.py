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


def test_validated_shop_order_requires_operator_sequence():
    app = _app()
    dialog = LoginDialog()
    dialog.operator_id_input.setText('OP1')

    dialog.work_order_details = {
        'ShopOrder': '51034643',
        'PartID': 'CERBERUS-575T-SEI',
        'SequenceID': '',
        'OrderQTY': 40,
        'OrderQty': 40,
    }
    dialog._update_details(dialog.work_order_details)
    app.processEvents()

    assert dialog.part_id_input.text() == 'CERBERUS-575T-SEI'
    assert dialog.order_qty_input.text() == '40'
    assert dialog.sequence_input.text() == ''
    assert dialog.login_button.isEnabled() is False

    dialog.sequence_input.setText('300')
    app.processEvents()

    assert dialog.login_button.isEnabled() is True

    dialog.validation_timer.stop()
    dialog.close()


def test_validated_shop_order_overwrites_stray_part_edit():
    app = _app()
    dialog = LoginDialog()
    dialog.operator_id_input.setText('OP1')
    dialog.part_id_input.setReadOnly(False)
    dialog.part_id_input.setText('200300')
    dialog._part_id_user_edited = True

    dialog.work_order_details = {
        'ShopOrder': '51026425',
        'PartID': 'SPS-17123',
        'SequenceID': '300',
        'OrderQTY': 40,
        'OrderQty': 40,
    }
    dialog._update_details(dialog.work_order_details)
    app.processEvents()

    assert dialog.part_id_input.text() == 'SPS-17123'
    assert dialog.part_id_input.isReadOnly() is True
    assert dialog.login_button.isEnabled() is True

    dialog.validation_timer.stop()
    dialog.close()


def test_validated_login_rejects_part_mismatch():
    app = _app()
    dialog = LoginDialog()
    warnings: list[tuple[str, str]] = []
    dialog._show_warning_dialog = lambda title, message: warnings.append((title, message))
    emitted: list[dict] = []
    dialog.loginSuccessful.connect(emitted.append)

    dialog.operator_id_input.setText('OP1')
    dialog.shop_order_input.setText('51026425')
    dialog.work_order_details = {
        'ShopOrder': '51026425',
        'PartID': 'SPS-17123',
        'SequenceID': '300',
        'OrderQTY': 40,
        'OrderQty': 40,
    }
    dialog._update_details(dialog.work_order_details)
    dialog.part_id_input.setText('200300')
    app.processEvents()

    dialog.attempt_login()

    assert emitted == []
    assert warnings
    assert warnings[-1][0] == 'Part ID Mismatch'
    assert dialog.part_id_input.text() == 'SPS-17123'

    dialog.validation_timer.stop()
    dialog.close()


def test_validated_login_emits_authoritative_part_with_entered_sequence():
    app = _app()
    dialog = LoginDialog()
    emitted: list[dict] = []
    dialog.loginSuccessful.connect(emitted.append)

    dialog.operator_id_input.setText('OP1')
    dialog.shop_order_input.setText('51026425')
    dialog.work_order_details = {
        'ShopOrder': '51026425',
        'PartID': 'SPS-17123',
        'SequenceID': '',
        'OrderQTY': 40,
        'OrderQty': 40,
    }
    dialog._update_details(dialog.work_order_details)
    dialog.sequence_input.setText('300')
    app.processEvents()

    dialog.attempt_login()

    assert emitted
    assert emitted[0]['PartID'] == 'SPS-17123'
    assert emitted[0]['SequenceID'] == '300'

    dialog.validation_timer.stop()
    dialog.close()


def test_manual_entry_still_allows_sequence_and_part():
    app = _app()
    dialog = LoginDialog()
    dialog.operator_id_input.setText('OP1')
    dialog.shop_order_input.setText('51039999')
    dialog._manual_entry_mode = True
    dialog._prepare_manual_entry()

    assert dialog.login_button.isEnabled() is False

    dialog.part_id_input.setText('PART-1')
    dialog.sequence_input.setText('300')
    app.processEvents()

    assert dialog.login_button.isEnabled() is True

    dialog.validation_timer.stop()
    dialog.close()
