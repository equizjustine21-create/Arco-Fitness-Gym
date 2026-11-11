import sys

from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QVBoxLayout,
)


class AdminPasswordDialog(QDialog):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Admin password required")
        self.setModal(True)
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 24, 26, 24)
        layout.setSpacing(13)

        heading = QLabel("Admin password required")
        heading.setObjectName("dialogHeading")
        layout.addWidget(heading)

        message = QLabel("Enter the admin password to leave Walk-in attendance.")
        message.setWordWrap(True)
        layout.addWidget(message)

        layout.addWidget(QLabel("Admin password"))
        self.password_input = QLineEdit()
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_input.setPlaceholderText("Enter admin password")
        self.password_input.setClearButtonEnabled(True)
        layout.addWidget(self.password_input)

        buttons = QDialogButtonBox()
        buttons.addButton("Stay here", QDialogButtonBox.ButtonRole.RejectRole)
        verify_button = buttons.addButton(
            "Verify and go back", QDialogButtonBox.ButtonRole.AcceptRole
        )
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        self.password_input.returnPressed.connect(self.accept)
        verify_button.setDefault(True)
        layout.addWidget(buttons)
        self.password_input.setFocus()

        self.setStyleSheet(
            """
            QDialog {
                background: #ffffff;
                color: #252936;
            }
            QLabel {
                color: #686d78;
                font-size: 13px;
            }
            QLabel#dialogHeading {
                color: #252936;
                font-size: 21px;
                font-weight: 700;
            }
            QLineEdit {
                min-height: 46px;
                padding: 0 11px;
                border: 1px solid #dfe2e8;
                border-radius: 9px;
                color: #252936;
                font-size: 14px;
            }
            QLineEdit:focus {
                border: 2px solid #d91b1b;
            }
            QPushButton {
                min-height: 40px;
                padding: 0 13px;
                border: 1px solid #dfe2e8;
                border-radius: 8px;
                background: #ffffff;
                color: #454a56;
                font-weight: 600;
            }
            QPushButton:hover {
                background: #f5f6f8;
            }
            QPushButton:default {
                border-color: #d91b1b;
                background: #d91b1b;
                color: #ffffff;
            }
            """
        )

    def password(self):
        return self.password_input.text()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("ARCO FITNESS Admin Verification")
    dialog = AdminPasswordDialog()
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return 2
    sys.stdout.buffer.write(dialog.password().encode("utf-8"))
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
