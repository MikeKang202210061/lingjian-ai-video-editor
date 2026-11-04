"""Desktop presentation and keyboard focus rules, separate from editing logic.

The existing inspector pages and monitor widgets are reused. Their indices,
editing callbacks, and player objects remain unchanged.
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QSignalBlocker, Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QAbstractSpinBox, QApplication, QComboBox, QFormLayout, QFrame, QHBoxLayout, QLabel,
    QLineEdit, QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy, QStackedWidget,
    QTabWidget, QTextEdit, QVBoxLayout, QWidget,
)


class EditingFocusGuard(QObject):
    """Allow text controls to handle typing before window-wide editing actions."""
    def eventFilter(self, watched, event):
        if event.type() == QEvent.ShortcutOverride:
            focus = QApplication.focusWidget()
            if isinstance(focus, (QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox, QComboBox)):
                key = event.key()
                modifiers = event.modifiers()
                plain = not (modifiers & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier))
                text_key = (Qt.Key_A <= key <= Qt.Key_Z or key in
                            (Qt.Key_Space, Qt.Key_Delete, Qt.Key_Backspace, Qt.Key_Left,
                             Qt.Key_Right, Qt.Key_Home, Qt.Key_End))
                local_edit = bool(modifiers & (Qt.ControlModifier | Qt.MetaModifier)) and key in (
                    Qt.Key_A, Qt.Key_B, Qt.Key_C, Qt.Key_D, Qt.Key_V, Qt.Key_X, Qt.Key_Y, Qt.Key_Z)
                if (plain and text_key) or local_edit:
                    event.accept()
                    return True
        return super().eventFilter(watched, event)


class MonitorWorkspace(QStackedWidget):
    def __init__(self, splitter):
        super().__init__()
        self.source, self.program = splitter.widget(0), splitter.widget(1)
        self.titles = (self.source.title(), self.program.title())
        self.splitter = splitter
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.addWidget(self.tabs)
        self.addWidget(splitter)
        self.set_dual(False)

    def set_dual(self, dual):
        for widget, title in zip((self.source, self.program), self.titles):
            widget.setParent(None)
            widget.setTitle(title if dual else '')
            widget.setStyleSheet('' if dual else 'QGroupBox{margin-top:0;padding:6px;border:0}')
        if dual:
            self.splitter.addWidget(self.source)
            self.splitter.addWidget(self.program)
            self.splitter.setSizes([500, 500])
            self.setCurrentWidget(self.splitter)
        else:
            self.tabs.addTab(self.source, '源素材 · 审片与入出点')
            self.tabs.addTab(self.program, '节目 · 片段预览')
            self.tabs.setTabToolTip(1, '查看选中的时间线片段；字幕、转场、蒙版等完整效果需要渲染预览。')
            self.tabs.setCurrentIndex(1)
            self.setCurrentWidget(self.tabs)

    def show_source(self, *_):
        self.tabs.setCurrentIndex(0)

    def show_program(self, *_):
        self.tabs.setCurrentIndex(1)


def _reflow_timeline_tools(window):
    card = window.timeline_space_btn.parentWidget()
    old = card.layout()
    widgets = []
    while old.count():
        item = old.takeAt(0)
        if item.widget():
            widgets.append(item.widget())
    # Drop the old layout before giving the card a new one.
    holder = QWidget()
    holder.setLayout(old)
    layout = QVBoxLayout(card)
    layout.setContentsMargins(8, 5, 8, 5)
    layout.setSpacing(4)
    top, tools = QHBoxLayout(), QHBoxLayout()
    for widget in widgets:
        if isinstance(widget, QLabel):
            widget.setText('<b>时间线</b>')
            top.addWidget(widget)
        elif widget in (window.timeline_space_btn, window.global_tc, window.zoom):
            top.addWidget(widget)
        else:
            tools.addWidget(widget)
    top.insertStretch(1)
    tools.addStretch()
    layout.addLayout(top)
    layout.addLayout(tools)
    holder.deleteLater()


def install_workspace(window):
    window.setMinimumSize(1120, 680)
    window.title.setMaximumWidth(210)
    window.title.setAccessibleName('工程名称')
    window.search.setAccessibleName('搜索素材')
    window.search.setClearButtonEnabled(True)
    window.timeline.setAccessibleName('多轨编辑时间线')
    window.timeline.setFocusPolicy(Qt.StrongFocus)
    window.timeline_scroll.setMinimumHeight(200)
    window.main_workspace.setChildrenCollapsible(False)
    window.main_workspace.widget(0).setMinimumWidth(190)
    window.main_workspace.widget(0).setMaximumWidth(350)
    window.main_workspace.widget(1).setMinimumWidth(500)
    _reflow_timeline_tools(window)

    monitors = window.monitors_workspace
    layout = monitors.parentWidget().layout()
    layout.removeWidget(monitors)
    window.monitor_workspace = MonitorWorkspace(monitors)
    layout.insertWidget(0, window.monitor_workspace, 1)
    window.media.currentItemChanged.connect(window.monitor_workspace.show_source)
    window.timeline.selected.connect(window.monitor_workspace.show_program)

    inspector = window.inspector
    window.cloud_btn.setToolTip(window.cloud_btn.text())
    window.cloud_btn.setText('生成 AI 剪辑方案')
    window.deep_btn.setToolTip(window.deep_btn.text())
    window.deep_btn.setText('离线生成剪辑方案')
    window.api_refine.setToolTip(window.api_refine.text())
    window.api_refine.setText('启用候选二次精看')
    for control in inspector.findChildren(QWidget):
        if isinstance(control, (QComboBox, QLineEdit, QTextEdit, QPlainTextEdit, QPushButton)):
            policy = control.sizePolicy()
            policy.setHorizontalPolicy(QSizePolicy.Ignored)
            control.setSizePolicy(policy)
            control.setMinimumWidth(0)
        if isinstance(control, QComboBox):
            control.setMinimumContentsLength(6)
            control.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            if not control.toolTip():
                control.setToolTip(control.currentText())
                control.currentTextChanged.connect(control.setToolTip)
        if isinstance(control, QPushButton) and not control.toolTip():
            control.setToolTip(control.text())
    for form in inspector.findChildren(QFormLayout):
        form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
    # The inspector keeps its original page ordering for existing controllers.
    for index in range(inspector.count()):
        page = inspector.widget(index)
        if isinstance(page, QScrollArea):
            continue
        label = inspector.tabText(index)
        inspector.removeTab(index)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        page.setMinimumWidth(0)
        scroll.setWidget(page)
        inspector.insertTab(index, scroll, label)
    inspector.setMinimumWidth(0)
    inspector.tabBar().hide()
    inspector.setCurrentIndex(0)
    panel = QFrame()
    window.inspector_host = panel
    panel.setObjectName('glassCard')
    panel.setMinimumWidth(290)
    panel.setMaximumWidth(460)
    window.main_workspace.replaceWidget(2, panel)
    panel_layout = QVBoxLayout(panel)
    panel_layout.setContentsMargins(10, 10, 10, 10)
    panel_layout.addWidget(QLabel('<b>工具与参数</b>'))
    window.inspector_selector = QComboBox()
    window.inspector_selector.setAccessibleName('选择工具面板')
    window.inspector_selector.addItems([
        '剪辑 · 裁切与音量', '字幕与标题', '转场', '蒙版与人物效果',
        'AI 导演 · 生成与检查方案', 'AI 接口设置', '音频与导出', '多轨合成',
    ])
    window.inspector_selector.currentIndexChanged.connect(inspector.setCurrentIndex)
    def sync(index):
        with QSignalBlocker(window.inspector_selector):
            window.inspector_selector.setCurrentIndex(index)
    inspector.currentChanged.connect(sync)
    panel_layout.addWidget(window.inspector_selector)
    panel_layout.addWidget(inspector, 1)

    window.mode_chip.hide()
    window.workspace_selector = QComboBox()
    window.workspace_selector.setAccessibleName('工作区布局')
    window.workspace_selector.addItems(['剪辑工作区', '双屏审片'])
    window.workspace_selector.setMaximumWidth(150)
    window.mode_chip.parentWidget().layout().insertWidget(3, window.workspace_selector)
    def switch_mode(index):
        window.monitor_workspace.set_dual(index == 1)
        window.settings.setValue('workspace/dual', bool(index))
    window.workspace_selector.currentIndexChanged.connect(switch_mode)
    if str(window.settings.value('workspace/dual', 'false')).lower() in ('true', '1'):
        window.workspace_selector.setCurrentIndex(1)
    window.main_workspace.setSizes([220, 780, 330])
    window.vertical_workspace.setSizes([340, 270])

    window.focus_guard = EditingFocusGuard(window)
    QApplication.instance().installEventFilter(window.focus_guard)
    file_menu = window.menuBar().addMenu('文件(&F)')
    for text, key, callback in (
        ('导入素材…', 'Ctrl+I', window.import_files),
        ('打开工程…', 'Ctrl+O', window.open_project),
        ('工程另存为…', 'Ctrl+Shift+S', window.save_project_as),
    ):
        action = QAction(text, window)
        action.setShortcut(QKeySequence(key))
        action.triggered.connect(callback)
        file_menu.addAction(action)
    view_menu = window.menuBar().addMenu('工作区(&V)')
    for text, index in (('剪辑', 0), ('AI 导演', 4), ('音频与导出', 6)):
        action = QAction(text, window)
        action.triggered.connect(lambda checked=False, i=index: inspector.setCurrentIndex(i))
        view_menu.addAction(action)
