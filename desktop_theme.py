"""Desktop colors and widget styles, kept separate from application workflows."""

STYLE='''
QMainWindow,QWidget#appRoot{background:#EEF3F8;color:#172033;font:13px "Microsoft YaHei UI"}
QLabel{background:transparent;color:#263247}
QFrame#glassCard,QFrame#mainStage{background:rgba(255,255,255,238);border:1px solid rgba(255,255,255,250);border-radius:12px}
QFrame#topGlass{background:rgba(255,255,255,224);border:1px solid #FFFFFF;border-bottom:1px solid #DCE5EF;border-radius:12px}
QFrame#toolGlass{background:rgba(245,248,252,230);border:1px solid #DCE5EF;border-radius:12px}
QLabel#stepGuide{background:rgba(255,255,255,210);color:#5D697C;border:1px solid #DCE5EF;border-radius:12px;padding:10px 16px}
QLabel#statusChip{background:#E8F2FF;color:#1769C2;border:1px solid #C9E0FA;border-radius:11px;padding:5px 10px;font-weight:600}
QPushButton{min-height:18px;background:rgba(255,255,255,210);color:#243044;border:1px solid #D6E0EB;border-top:1px solid #FFFFFF;border-radius:10px;padding:7px 12px;font-weight:500}
QPushButton:hover{background:#FFFFFF;border-color:#9EC5F2;color:#0B62BD}
QPushButton:pressed{background:#E7EFF8;border-color:#7EB3EB;padding-top:8px}
QPushButton:disabled{background:#EDF1F5;color:#AAB3BF;border-color:#E3E8EE}
QPushButton#primary{background:#1477E6;color:white;border:1px solid #0D69D0;border-top:1px solid #69ADFA;font-weight:700}
QPushButton#primary:hover{background:#2588F3;border-color:#0C6CD7}
QPushButton#danger{color:#C63D4E;background:#FFF5F6;border-color:#F4D3D8}
QLineEdit,QTextEdit,QPlainTextEdit,QDoubleSpinBox,QSpinBox,QComboBox{background:rgba(255,255,255,242);color:#1E293B;border:1px solid #D5DFEA;border-top:1px solid #FFFFFF;border-radius:9px;padding:7px;selection-background-color:#A9CFF7}
QLineEdit:focus,QTextEdit:focus,QPlainTextEdit:focus,QDoubleSpinBox:focus,QSpinBox:focus,QComboBox:focus{border:1px solid #4C9AF0;background:#FFFFFF}
QComboBox::drop-down{border:0;width:24px}
QListWidget{background:rgba(255,255,255,225);color:#263247;border:1px solid #DDE5EE;border-radius:12px;outline:0;padding:4px}
QListWidget::item{padding:9px;border:0;border-bottom:1px solid #EDF1F5;border-radius:8px}
QListWidget::item:hover{background:#F0F6FD}
QListWidget::item:selected{background:#DCEEFF;color:#135DAB;border-left:3px solid #2685E8}
QTabWidget#inspectorTabs::pane{background:rgba(255,255,255,238);border:1px solid #DCE5EF;border-radius:10px;top:-1px}
QTabWidget#inspectorTabs QTabBar::tab{background:transparent;color:#718096;border:0;padding:9px 10px;margin:2px 0}
QTabWidget#inspectorTabs QTabBar::tab:hover{color:#1769C2;background:#EDF6FF;border-radius:8px}
QTabWidget#inspectorTabs QTabBar::tab:selected{color:#0F67C4;background:#E2F0FF;border-radius:8px;font-weight:700}
QGroupBox{background:rgba(255,255,255,220);color:#36445A;border:1px solid #DCE5EF;border-radius:13px;margin-top:14px;padding:16px 11px 11px;font-weight:600}
QGroupBox::title{subcontrol-origin:margin;left:14px;padding:0 6px;color:#536176;background:#F7FAFD}
QSlider::groove:horizontal{height:5px;background:#D8E2EC;border-radius:2px}
QSlider::sub-page:horizontal{background:#69A9EF;border-radius:2px}
QSlider::handle:horizontal{width:16px;margin:-6px 0;background:#FFFFFF;border:2px solid #2080E5;border-radius:8px}
QProgressBar{background:#E2E9F1;color:#405064;border:0;border-radius:6px;text-align:center}
QProgressBar::chunk{background:#3B93ED;border-radius:6px}
QScrollArea{background:transparent;border:0}
QSplitter::handle{background:#DCE5EF;width:5px;height:5px}
QSplitter::handle:hover{background:#9EC5F2}
QPushButton:focus{border:2px solid #1769C2}
QTabWidget::pane{border:0;background:transparent}
QTabBar::tab{padding:7px 10px;color:#536176}
QTabBar::tab:selected{color:#1769C2;background:#E8F2FF}
QMenuBar{background:#F8FAFC;color:#36445A}
QMenuBar::item:selected,QMenu::item:selected{background:#E8F2FF;color:#1769C2}
QStatusBar{background:#F8FAFC;color:#607086;border-top:1px solid #DCE4ED}
QToolTip{background:#172033;color:white;border:1px solid #42506A;padding:6px;border-radius:6px}
'''
