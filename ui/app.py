import datetime
import os
import sys

import pyqtgraph as pg
import serial
from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QIntValidator
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
    QGraphicsDropShadowEffect,
)

from voltage_logger.core.bus import EventBus
from voltage_logger.core.config import DEFAULT_BAUDRATE, WAVEFORM_REFRESH_MS, get_data_dir
from voltage_logger.core.utils import format_readable, get_available_ports
from voltage_logger.services.logger_service import CsvLogger, TxtLogger
from voltage_logger.services.report_service import ReportGenerator
from voltage_logger.services.serial_service import SerialReader
from voltage_logger.services.statistics_service import StatisticsManager
from voltage_logger.services.waveform_service import WaveformManager
from voltage_logger.ui.theme import WINDOW_TITLE


class UiSignals(QObject):
    status = Signal(str)
    log = Signal(str)
    data = Signal(object, object, object, object, object, object)
    stat_complete = Signal(int, float)
    reader_finished = Signal()


class ExternalArrowCombo(QWidget):
    currentTextChanged = Signal(str)

    def __init__(self, values=None, parent=None):
        super().__init__(parent)
        self._values = list(values or [])
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self._edit = QLineEdit()
        self._edit.textChanged.connect(self.currentTextChanged)
        self._edit.textChanged.connect(self._resize_to_text)
        self._arrow = QPushButton("▼")
        self._arrow.setFixedSize(20, 34)
        self._arrow.setStyleSheet(
            "QPushButton { background:#FAFAFC; color:#555555; border:1px solid #D6D7DB; "
            "border-radius:7px; padding:2px; }"
            "QPushButton:hover { background:#FFFFFF; border-color:#B8BAC0; }"
        )
        self._arrow.clicked.connect(self.showPopup)
        layout.addWidget(self._edit)
        layout.addWidget(self._arrow)
        self.setMinimumWidth(86)

    def lineEdit(self):
        return self._edit

    def currentText(self):
        return self._edit.text()

    def setCurrentText(self, text):
        self._edit.setText(text)

    def clear(self):
        self._edit.clear()

    def addItems(self, values):
        self._values = list(values)

    def setCompleter(self, completer):
        self._edit.setCompleter(completer)

    def showPopup(self):
        if not self._values:
            return
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu { background:#FFFFFF; border:1px solid #D6D7DB; border-radius:7px; padding:4px; }"
            "QMenu::item { padding:6px 18px; border-radius:4px; }"
            "QMenu::item:selected { background:#EAF3FF; color:#0066CC; }"
        )
        for value in self._values:
            action = menu.addAction(value)
            action.triggered.connect(lambda checked=False, item=value: self.setCurrentText(item))
        menu.exec(self._arrow.mapToGlobal(self._arrow.rect().bottomLeft()))

    def _resize_to_text(self, text):
        width = self._edit.fontMetrics().horizontalAdvance(text or "COM25") + 28
        self._edit.setFixedWidth(max(width, 60))


class VoltagePlot(pg.PlotWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setBackground("#FFFFFF")
        self.showGrid(x=True, y=True, alpha=0.18)
        self.setLabel("left", "电压", units="V")
        self.setLabel("bottom", "时间", units="s")
        self.getAxis("left").setPen("#7A7A7A")
        self.getAxis("bottom").setPen("#7A7A7A")
        self.getAxis("left").setTextPen("#555555")
        self.getAxis("bottom").setTextPen("#555555")
        self.curve = self.plot([], [], pen=pg.mkPen("#0066CC", width=2))
        self.points = pg.ScatterPlotItem(size=7, brush="#0066CC", pen=pg.mkPen("#FFFFFF", width=1))
        self.addItem(self.points)
        self.hover_text = pg.TextItem(color="#1D1D1F", anchor=(0, 1), fill="#F5F5F7")
        self.addItem(self.hover_text)
        self.hover_text.hide()
        self.scene().sigMouseMoved.connect(self._on_mouse_moved)
        self._data = []

    def set_data(self, data):
        self._data = data
        if not data:
            self.curve.setData([], [])
            self.points.setData([])
            self.hover_text.hide()
            return
        now = datetime.datetime.now()
        x = [(ts - now).total_seconds() for ts, _ in data]
        y = [value for _, value in data]
        self.curve.setData(x, y)
        self.points.setData(x=x, y=y)
        self.setXRange(-60, 0, padding=0.02)
        self.hover_text.hide()

    def _on_mouse_moved(self, position):
        if not self._data or not self.sceneBoundingRect().contains(position):
            self.hover_text.hide()
            return
        point = self.plotItem.vb.mapSceneToView(position)
        nearest = min(self._data, key=lambda item: abs((item[0] - datetime.datetime.now()).total_seconds() - point.x()))
        x = (nearest[0] - datetime.datetime.now()).total_seconds()
        self.hover_text.setText(f"{nearest[0].strftime('%H:%M:%S')}  {nearest[1]:.6f} V")
        self.hover_text.setPos(x, nearest[1])
        self.hover_text.show()


class App(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(WINDOW_TITLE)
        self.resize(1280, 960)
        self.setMinimumSize(1000, 720)
        self._apply_style()

        self.event_bus = EventBus()
        self.signals = UiSignals()
        self.event_bus.register("status", self.signals.status.emit)
        self.event_bus.register("log", self.signals.log.emit)
        self.event_bus.register("data_received", self.signals.data.emit)
        self.event_bus.register("stat_complete", self.signals.stat_complete.emit)
        self.event_bus.register("reader_finished", self.signals.reader_finished.emit)

        self.report_gen = ReportGenerator(self.event_bus)
        self.txt_logger = TxtLogger(self.event_bus)
        self.csv_logger = CsvLogger(self.event_bus, self.report_gen)
        self.stat_mgr = StatisticsManager(self.event_bus)
        self.wave_mgr = WaveformManager(self.event_bus)
        self.serial_reader = None
        self.is_connected = False
        self.is_reading = False
        self._log_paused = False

        self.signals.status.connect(self.update_status)
        self.signals.log.connect(self.append_log)
        self.signals.data.connect(self.on_data_received)
        self.signals.stat_complete.connect(self.on_stat_complete)
        self.signals.reader_finished.connect(self._reset_reader_ui)
        self._build_ui()
        self.refresh_ports()
        self.select_channel(0)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_waveform)
        self.timer.start(WAVEFORM_REFRESH_MS)

    def _apply_style(self):
        self.setStyleSheet("""
            QWidget { background: #F5F5F7; color: #1D1D1F; font-family: "Segoe UI", Arial, sans-serif; font-size: 13px; }
            QLabel, QCheckBox { background: transparent; }
            QGroupBox { background: #FFFFFF; border: 1px solid #DCDDE1; border-radius: 10px; margin-top: 12px; padding: 16px 12px 12px; font-weight: 600; }
            QGroupBox::title { subcontrol-origin: margin; left: 14px; padding: 0 6px; color: #1D1D1F; }
            QPushButton { background: #FAFAFC; color: #1D1D1F; border: 1px solid #D6D7DB; border-radius: 7px; padding: 7px 14px; }
            QPushButton:hover { background: #FFFFFF; border-color: #B8BAC0; }
            QPushButton:pressed { background: #E8E9ED; padding-top: 8px; padding-left: 15px; }
            QPushButton:disabled { color: #999999; background: #F0F0F2; }
            QLineEdit, QSpinBox { background: #FFFFFF; border: 1px solid #D6D7DB; border-radius: 7px; padding: 6px 9px; min-height: 20px; }
            QPlainTextEdit { background: #FFFFFF; border: 1px solid #D6D7DB; border-radius: 8px; padding: 8px; }
            QCheckBox::indicator { width: 16px; height: 16px; }
        """)

    def _button(self, text, slot, primary=False):
        button = QPushButton(text)
        button.clicked.connect(slot)
        if primary:
            button.setStyleSheet("QPushButton { background:#0066CC; color:white; border:1px solid #005BB5; } QPushButton:hover { background:#0071E3; }")
        effect = QGraphicsDropShadowEffect(self)
        effect.setBlurRadius(8)
        effect.setOffset(0, 2)
        effect.setColor(QColor(0, 0, 0, 35))
        button.setGraphicsEffect(effect)
        return button

    def _set_state_button(self, button, active):
        if active:
            button.setStyleSheet(
                "QPushButton { background:#D70015; color:white; border:1px solid #B00012; }"
                "QPushButton:hover { background:#B00012; }"
            )
        else:
            button.setStyleSheet(
                "QPushButton { background:#0066CC; color:white; border:1px solid #005BB5; }"
                "QPushButton:hover { background:#0071E3; }"
            )

    def _group(self, title):
        return QGroupBox(title)

    def _strong_label(self, text):
        label = QLabel(text)
        label.setFont(QFont("Segoe UI", 13, QFont.Bold))
        return label

    def _resize_port_combo(self, text):
        text_width = self.port_combo.fontMetrics().horizontalAdvance(text or "COM25")
        self.port_combo.setFixedWidth(max(text_width + 34, 86))

    def _resize_baud_combo(self, text):
        text_width = self.baud_combo.fontMetrics().horizontalAdvance(text or str(DEFAULT_BAUDRATE))
        self.baud_combo.setFixedWidth(max(text_width + 34, 94))

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)

        serial_box = self._group("串口连接")
        serial_layout = QHBoxLayout(serial_box)
        serial_layout.addWidget(self._strong_label("串口号"))
        self.port_combo = ExternalArrowCombo()
        self.port_combo.lineEdit().setPlaceholderText("如 COM25")
        self.port_combo.currentTextChanged.connect(self._resize_port_combo)
        serial_layout.addWidget(self.port_combo)
        serial_layout.addWidget(self._button("刷新", self.refresh_ports))
        serial_layout.addWidget(self._strong_label("波特率"))
        self.baud_combo = ExternalArrowCombo(["9600", "19200", "38400", "57600", "115200", "230400"])
        self.baud_combo.setCurrentText(str(DEFAULT_BAUDRATE))
        self.baud_combo.currentTextChanged.connect(self._resize_baud_combo)
        serial_layout.addWidget(self.baud_combo)
        serial_layout.addWidget(self._strong_label("状态"))
        self.status_label = QLabel("断开")
        self.status_label.setFont(QFont("Segoe UI", 13, QFont.Bold))
        self.status_label.setStyleSheet("color:#D70015; font-weight:600;")
        serial_layout.addWidget(self.status_label)
        self.btn_connect = self._button("连接", self.toggle_connect, primary=True)
        serial_layout.addWidget(self.btn_connect)
        serial_layout.addWidget(self._button("日志目录", self.open_log_folder))
        serial_layout.addWidget(self._button("均值目录", self.open_mean_folder))
        serial_layout.addStretch()
        root.addWidget(serial_box)
        self._resize_port_combo(self.port_combo.currentText())
        self._resize_baud_combo(self.baud_combo.currentText())

        command_box = self._group("命令配置及记录")
        command_grid = QGridLayout(command_box)
        command_grid.setContentsMargins(8, 6, 8, 6)
        command_grid.setHorizontalSpacing(8)
        command_grid.setVerticalSpacing(6)
        self.loop_spin = QLineEdit("5")
        self.loop_spin.setValidator(QIntValidator(1, 999999, self.loop_spin))
        self.loop_spin.setFixedWidth(55)
        self.interval_spin = QLineEdit("1")
        self.interval_spin.setValidator(QIntValidator(1, 999999, self.interval_spin))
        self.interval_spin.setFixedWidth(55)
        self.continuous_check = QCheckBox("持续循环")
        self.custom_check = QCheckBox("启用自定义命令")
        checkbox_font = QFont("Segoe UI", 13, QFont.Bold)
        self.continuous_check.setFont(checkbox_font)
        self.custom_check.setFont(checkbox_font)
        self.custom_edit = QLineEdit()
        self.custom_edit.setEnabled(False)
        self.custom_check.toggled.connect(self.toggle_custom_command)
        self.loop_spin.textChanged.connect(self.update_cmd_display)
        self.interval_spin.textChanged.connect(self.update_cmd_display)
        self.custom_edit.textChanged.connect(self.update_cmd_display)
        command_grid.addWidget(self._strong_label("循环"), 0, 0)
        command_grid.addWidget(self.loop_spin, 0, 1)
        command_grid.addWidget(self._strong_label("间隔"), 0, 2)
        command_grid.addWidget(self.interval_spin, 0, 3)
        command_grid.addWidget(self.continuous_check, 0, 4)
        command_grid.addWidget(self.custom_check, 0, 5)
        command_grid.addWidget(self.custom_edit, 0, 6)
        self.cmd_display = QLabel()
        self.cmd_display.setStyleSheet("color:#0066CC; font-family:Consolas; font-size:14px;")
        command_grid.addWidget(self._strong_label("命令"), 0, 7)
        command_grid.addWidget(self.cmd_display, 0, 8)
        self.btn_exec = self._button("▶ 执行", self.toggle_exec, primary=True)
        self.btn_exec.setEnabled(False)
        command_grid.addWidget(self.btn_exec, 0, 9)
        self.btn_txt_toggle = self._button("开始 TXT", self.toggle_txt_recording)
        self.btn_csv_toggle = self._button("开启 CSV", self.toggle_csv)
        self.btn_txt_toggle.setEnabled(False)
        self.btn_csv_toggle.setEnabled(False)
        self._set_state_button(self.btn_txt_toggle, active=False)
        self._set_state_button(self.btn_csv_toggle, active=False)
        command_grid.addWidget(self.btn_txt_toggle, 0, 10)
        command_grid.addWidget(self.btn_csv_toggle, 0, 11)
        command_grid.setColumnStretch(6, 1)
        root.addWidget(command_box)

        self.channel_value_labels = []
        self.channel_avg_labels = []
        self.channel_count_edits = []
        self.channel_stat_buttons = []
        stats_box = self._group("通道数值统计")
        stats_grid = QGridLayout(stats_box)
        stats_grid.setContentsMargins(6, 6, 6, 6)
        stats_grid.setHorizontalSpacing(8)
        stats_grid.setVerticalSpacing(8)
        for i in range(10):
            card = QFrame()
            card.setStyleSheet("QFrame { background:#FAFAFC; border:1px solid #E5E5E8; border-radius:8px; }")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(10, 7, 10, 7)
            card_layout.setSpacing(4)
            value = QLabel(f"通道{i}: 等待数据...")
            value.setStyleSheet("color:#0066CC; font-size:13px; font-weight:600;")
            card_layout.addWidget(value)
            self.channel_value_labels.append(value)
            controls = QHBoxLayout()
            controls.setContentsMargins(0, 0, 0, 0)
            controls.setSpacing(3)
            count = QLineEdit("10")
            count.setPlaceholderText("次数")
            count.setFixedWidth(52)
            count.setFixedHeight(34)
            count.setValidator(QIntValidator(1, 999999, count))
            button = self._button("统计均值", lambda checked=False, ch=i: self.start_statistics(ch))
            control_height = 34
            button.setFixedHeight(control_height)
            button.setFixedWidth(button.sizeHint().width())
            avg = QLabel("---")
            avg.setStyleSheet("color:#0066CC; font-size:13px; font-weight:600; border:none; padding:0;")
            avg.setFrameStyle(QFrame.NoFrame)
            avg.setFixedHeight(control_height)
            avg.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
            controls.addWidget(count)
            controls.addWidget(button)
            controls.addWidget(avg)
            controls.addStretch()
            card_layout.addLayout(controls)
            self.channel_count_edits.append(count)
            self.channel_stat_buttons.append(button)
            self.channel_avg_labels.append(avg)
            stats_grid.addWidget(card, i // 5, i % 5)
        root.addWidget(stats_box)

        log_box = self._group("串口打印")
        log_layout = QVBoxLayout(log_box)
        log_layout.setContentsMargins(10, 12, 10, 10)
        log_buttons = QHBoxLayout()
        self.btn_pause_log = self._button("暂停更新", self.toggle_log_pause)
        self.btn_clear_log = self._button("清空日志", self.clear_log)
        log_buttons.addWidget(self.btn_pause_log, 1)
        log_buttons.addWidget(self.btn_clear_log, 1)
        log_layout.addLayout(log_buttons)
        self.text_area = QPlainTextEdit()
        self.text_area.setReadOnly(True)
        log_layout.addWidget(self.text_area, 1)

        wave_box = self._group("通道波形显示（最近 1 分钟）")
        wave_layout = QVBoxLayout(wave_box)
        wave_layout.setContentsMargins(10, 12, 10, 10)
        channel_buttons_layout = QHBoxLayout()
        self.channel_buttons = []
        for i in range(10):
            button = self._button(str(i), lambda checked=False, ch=i: self.select_channel(ch))
            button.setFixedWidth(38)
            channel_buttons_layout.addWidget(button)
            self.channel_buttons.append(button)
        self.wave_realtime_label = QLabel("实时数据：---")
        self.wave_realtime_label.setStyleSheet(
            "color:#0066CC; font-size:12px; font-weight:600; padding-left:8px;"
        )
        channel_buttons_layout.addWidget(self.wave_realtime_label)
        channel_buttons_layout.addStretch()
        wave_layout.addLayout(channel_buttons_layout)
        self.wave_plot = VoltagePlot()
        wave_layout.addWidget(self.wave_plot, 1)

        display_splitter = QSplitter(Qt.Horizontal)
        display_splitter.setChildrenCollapsible(False)
        display_splitter.setHandleWidth(8)
        display_splitter.addWidget(log_box)
        display_splitter.addWidget(wave_box)
        display_splitter.setStretchFactor(0, 3)
        display_splitter.setStretchFactor(1, 7)
        display_splitter.setSizes([300, 700])
        display_splitter.setStyleSheet(
            "QSplitter::handle { background:#D6D7DB; border-radius:4px; }"
            "QSplitter::handle:hover { background:#0066CC; }"
        )
        root.addWidget(display_splitter, 2)
        self.status_bar = QLabel("💡就绪，请连接串口")
        self.status_bar.setStyleSheet("background:#FAFAFC; color:#7A7A7A; padding:8px 12px; border-radius:7px;")
        root.addWidget(self.status_bar)
        self.update_cmd_display()

    def update_status(self, msg):
        self.status_bar.setText(msg)

    def append_log(self, msg):
        if not self._log_paused:
            self.text_area.appendPlainText(msg)

    def on_data_received(self, display_msg, dut_num, value_display, voltage_value, timestamp_str, raw_line):
        if display_msg:
            self.append_log(display_msg)
        if dut_num is not None and value_display is not None:
            self.channel_value_labels[dut_num].setText(f"通道{dut_num}: {value_display} V")
            if dut_num == self.wave_mgr.selected_channel:
                self.wave_realtime_label.setText(
                    f"实时数据：{datetime.datetime.now().strftime('%H:%M:%S')}  {value_display} V"
                )

    def toggle_log_pause(self):
        self._log_paused = not self._log_paused
        self.btn_pause_log.setText("恢复更新" if self._log_paused else "暂停更新")

    def clear_log(self):
        self.text_area.clear()

    def on_stat_complete(self, channel, avg):
        button = self.channel_stat_buttons[channel]
        button.setEnabled(True)
        button.setText("统计均值")
        button.setStyleSheet("")
        self.channel_avg_labels[channel].setText(format_readable(avg))

    def _reset_reader_ui(self):
        if self.is_reading:
            self.is_reading = False
            self.serial_reader = None
            self.btn_exec.setText("▶ 执行")
            self.btn_exec.setStyleSheet("QPushButton { background:#0066CC; color:white; border:1px solid #005BB5; }")

    def refresh_ports(self):
        ports = get_available_ports()
        current = self.port_combo.currentText().strip()
        self.port_combo.clear()
        if ports:
            self.port_combo.addItems(ports)
            self.port_combo.setCurrentText(current if current in ports else ports[0])
            self.update_status(f"✅ 已扫描到 {len(ports)} 个串口")
        else:
            self.update_status("⚠️ 未检测到可用串口")

    def update_cmd_display(self):
        custom = self.custom_edit.text().strip() if self.custom_check.isChecked() else ""
        self.cmd_display.setText(custom or f"Read_ALLChannels({self.loop_spin.text() or 5},{self.interval_spin.text() or 1})")

    def toggle_custom_command(self, enabled):
        self.custom_edit.setEnabled(enabled)
        if not enabled:
            self.custom_edit.clear()
        self.update_cmd_display()

    def toggle_connect(self):
        self.disconnect_serial() if self.is_connected else self.connect_serial()

    def connect_serial(self):
        port = self.port_combo.currentText().strip()
        if not port:
            QMessageBox.critical(self, "错误", "请选择或输入串口号")
            return
        try:
            baudrate = int(self.baud_combo.currentText().strip())
            if baudrate <= 0:
                raise ValueError
        except ValueError:
            QMessageBox.critical(self, "错误", "波特率必须为正整数")
            return
        try:
            test_ser = serial.Serial(port, baudrate, timeout=1)
            test_ser.close()
        except Exception as exc:
            QMessageBox.critical(self, "错误", f"⛔无法打开串口 {port}: {exc}")
            return
        self.is_connected = True
        self.status_label.setText("已连接")
        self.status_label.setStyleSheet("color:#16803C; font-weight:600;")
        self.btn_connect.setText("断开")
        self.btn_connect.setStyleSheet("QPushButton { background:#D70015; color:white; border:1px solid #B00012; }")
        self.btn_exec.setEnabled(True)
        self.btn_txt_toggle.setEnabled(True)
        self.btn_csv_toggle.setEnabled(True)
        self._port, self._baudrate = port, baudrate
        self.update_status(f"🟢 串口 {port} 连接成功 (波特率 {baudrate})")
        self.append_log(f"[系统] 串口 {port} 连接成功")

    def disconnect_serial(self):
        if not self.is_connected:
            return
        if self.is_reading:
            self.stop_reader()
        if self.txt_logger.is_recording:
            self.txt_logger.stop_recording()
            self.btn_txt_toggle.setText("开始 TXT")
            self._set_state_button(self.btn_txt_toggle, active=False)
        if self.csv_logger.real_csv_enabled:
            self.csv_logger.stop_real_csv()
            self.btn_csv_toggle.setText("开启 CSV")
            self._set_state_button(self.btn_csv_toggle, active=False)
        self.is_connected = False
        self.status_label.setText("断开")
        self.status_label.setStyleSheet("color:#D70015; font-weight:600;")
        self.btn_connect.setText("连接")
        self.btn_connect.setStyleSheet("QPushButton { background:#0066CC; color:white; border:1px solid #005BB5; }")
        self.btn_exec.setEnabled(False)
        self.btn_txt_toggle.setEnabled(False)
        self.btn_csv_toggle.setEnabled(False)
        self.update_status("🔴串口已断开")

    def toggle_exec(self):
        if not self.is_connected:
            QMessageBox.warning(self, "提示", "请先连接串口")
        elif self.is_reading:
            self.stop_reader()
        else:
            self.start_reader()

    def start_reader(self):
        custom = self.custom_edit.text().strip() if self.custom_check.isChecked() else None
        self.serial_reader = SerialReader(
            self._port, self._baudrate, self.event_bus, int(self.loop_spin.text() or 5),
            int(self.interval_spin.text() or 1), custom, self.continuous_check.isChecked())
        self.serial_reader.start()
        self.is_reading = True
        self.btn_exec.setText("⏹ 停止")
        self.btn_exec.setStyleSheet("QPushButton { background:#D70015; color:white; border:1px solid #B00012; }")
        self.update_status("▶️ 命令发送已启动")

    def stop_reader(self):
        if self.serial_reader:
            self.serial_reader.stop()
            self.serial_reader = None
        self.is_reading = False
        self.btn_exec.setText("▶ 执行")
        self.btn_exec.setStyleSheet("QPushButton { background:#0066CC; color:white; border:1px solid #005BB5; }")
        self.update_status("⏹️ 命令发送已停止")

    def start_txt_recording(self):
        if not self.is_reading:
            QMessageBox.warning(self, "提示", "请先执行命令发送")
            return
        ok, msg = self.txt_logger.start_recording()
        if ok:
            self.btn_txt_toggle.setText("停止 TXT")
            self._set_state_button(self.btn_txt_toggle, active=True)
        else:
            QMessageBox.critical(self, "错误", msg)

    def stop_txt_recording(self):
        self.txt_logger.stop_recording()
        self.btn_txt_toggle.setText("开始 TXT")
        self._set_state_button(self.btn_txt_toggle, active=False)

    def toggle_txt_recording(self):
        if self.txt_logger.is_recording:
            self.stop_txt_recording()
        else:
            self.start_txt_recording()

    def toggle_csv(self):
        if not self.is_reading:
            QMessageBox.warning(self, "提示", "请先执行命令发送")
            return
        if self.csv_logger.real_csv_enabled:
            self.csv_logger.stop_real_csv()
            self.btn_csv_toggle.setText("开启 CSV")
            self._set_state_button(self.btn_csv_toggle, active=False)
        else:
            ok, msg = self.csv_logger.start_real_csv()
            if ok:
                self.btn_csv_toggle.setText("关闭 CSV")
                self._set_state_button(self.btn_csv_toggle, active=True)
            else:
                QMessageBox.critical(self, "错误", f"无法开启 CSV: {msg}")

    def start_statistics(self, channel):
        if not self.is_reading:
            QMessageBox.warning(self, "提示", "请先执行命令发送")
            return
        try:
            target = int(self.channel_count_edits[channel].text().strip())
            if target <= 0:
                raise ValueError
        except ValueError:
            QMessageBox.warning(self, "参数错误", "统计次数必须为正整数")
            return
        if self.stat_mgr.start(channel, target):
            button = self.channel_stat_buttons[channel]
            button.setEnabled(False)
            button.setStyleSheet(
                "QPushButton:disabled { background:#E8F1FF; color:#0066CC; "
                "border:1px solid #99BFEF; border-radius:7px; }"
            )
            self.channel_avg_labels[channel].setText("统计中…")

    def select_channel(self, channel):
        self.wave_mgr.set_channel(channel)
        for i, button in enumerate(self.channel_buttons):
            button.setStyleSheet(
                "QPushButton { background:#0066CC; color:white; border:1px solid #005BB5; }"
                if i == channel else "")
        self.update_status(f"📺 显示通道{channel} 波形")
        recent = self.wave_mgr.get_recent_data()
        if recent:
            timestamp, value = recent[-1]
            self.wave_realtime_label.setText(
                f"实时数据：{timestamp.strftime('%H:%M:%S')}  {value:.6f} V"
            )
        else:
            self.wave_realtime_label.setText("实时数据：---")

    def update_waveform(self):
        self.wave_plot.set_data(self.wave_mgr.get_recent_data())

    def open_log_folder(self):
        folder = os.path.join(get_data_dir(), "ChannelDataLogTXT")
        os.makedirs(folder, exist_ok=True)
        os.startfile(folder)

    def open_mean_folder(self):
        folder = os.path.join(get_data_dir(), "mean_statistics")
        if os.path.exists(folder):
            os.startfile(folder)
        else:
            QMessageBox.information(self, "提示", "均值统计目录尚未创建")

    def closeEvent(self, event):
        self.timer.stop()
        if self.is_reading:
            self.stop_reader()
        if self.txt_logger.is_recording:
            self.txt_logger.stop_recording()
        if self.csv_logger.real_csv_enabled:
            self.csv_logger.stop_real_csv()
        self.event_bus.close()
        event.accept()


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    window = App()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
