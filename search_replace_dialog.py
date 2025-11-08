from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QCheckBox,
    QPushButton,
    QApplication,
)


class SearchReplaceDialog(QDialog):
    search_requested = Signal(str, bool)
    replace_requested = Signal(str, str, bool)
    replace_all_requested = Signal(str, str, bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Search/Replace")
        self.setModal(False)
        self.resize(400, 200)

        self.last_search = ""
        self.last_replace = ""
        self.last_case_sensitive = False

        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        search_layout = QHBoxLayout()
        search_layout.addWidget(QLabel("Search:"))
        self.txt_search = QLineEdit()
        self.txt_search.setPlaceholderText("Enter text to search...")
        search_layout.addWidget(self.txt_search)
        layout.addLayout(search_layout)

        replace_layout = QHBoxLayout()
        self.chk_replace = QCheckBox("Replace")
        self.chk_replace.toggled.connect(self._on_replace_toggled)
        replace_layout.addWidget(self.chk_replace)

        self.txt_replace = QLineEdit()
        self.txt_replace.setPlaceholderText("Enter replacement text...")
        self.txt_replace.setEnabled(False)
        replace_layout.addWidget(self.txt_replace)
        layout.addLayout(replace_layout)

        self.chk_case_sensitive = QCheckBox("Case sensitive")
        layout.addWidget(self.chk_case_sensitive)

        btn_layout = QHBoxLayout()
        self.btn_search = QPushButton("Search")
        self.btn_search.clicked.connect(self._on_search_clicked)
        btn_layout.addWidget(self.btn_search)

        self.btn_replace = QPushButton("Replace")
        self.btn_replace.clicked.connect(self._on_replace_clicked)
        self.btn_replace.setEnabled(False)
        btn_layout.addWidget(self.btn_replace)

        self.btn_replace_all = QPushButton("Replace All")
        self.btn_replace_all.clicked.connect(self._on_replace_all_clicked)
        self.btn_replace_all.setEnabled(False)
        btn_layout.addWidget(self.btn_replace_all)

        layout.addLayout(btn_layout)

        self.txt_search.returnPressed.connect(self._on_search_clicked)
        self.txt_replace.returnPressed.connect(self._on_replace_clicked)

    def _on_replace_toggled(self, checked: bool):
        self.txt_replace.setEnabled(checked)
        self.btn_replace.setEnabled(checked)
        self.btn_replace_all.setEnabled(checked)

    def _on_search_clicked(self):
        search_text = self.txt_search.text().strip()
        if not search_text:
            return

        self.last_search = search_text
        self.last_case_sensitive = self.chk_case_sensitive.isChecked()
        self.search_requested.emit(search_text, self.chk_case_sensitive.isChecked())

    def _on_replace_clicked(self):
        search_text = self.txt_search.text().strip()
        replace_text = self.txt_replace.text()

        if not search_text:
            return

        self.last_search = search_text
        self.last_replace = replace_text
        self.last_case_sensitive = self.chk_case_sensitive.isChecked()
        self.replace_requested.emit(search_text, replace_text, self.chk_case_sensitive.isChecked())

    def _on_replace_all_clicked(self):
        search_text = self.txt_search.text().strip()
        replace_text = self.txt_replace.text()

        if not search_text:
            return

        self.last_search = search_text
        self.last_replace = replace_text
        self.last_case_sensitive = self.chk_case_sensitive.isChecked()
        self.replace_all_requested.emit(search_text, replace_text, self.chk_case_sensitive.isChecked())

    def showEvent(self, event):
        super().showEvent(event)
        self.txt_search.setText(self.last_search)
        self.txt_replace.setText(self.last_replace)
        self.chk_case_sensitive.setChecked(self.last_case_sensitive)
        self.txt_search.setFocus()
        self.txt_search.selectAll()

    def focusOutEvent(self, event):
        if not self.isAncestorOf(QApplication.focusWidget()):
            self.close()
        super().focusOutEvent(event)
