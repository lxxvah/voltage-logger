import datetime
import re
import threading
import time

import serial

from voltage_logger.core.config import DEFAULT_TIMEOUT, WRITE_TIMEOUT, READ_IDLE_TIMEOUT, MAX_READ_TIME, MAX_RETRIES, RECONNECT_DELAY
from voltage_logger.core.utils import extract_dut_number, extract_voltage_from_line, format_readable


class SerialReader:
    def __init__(self, port, baudrate, event_bus, cmd_loop, cmd_interval,
                 custom_command=None, continuous=False):
        self.port = port
        self.baudrate = baudrate
        self.event_bus = event_bus
        self.cmd_loop = int(cmd_loop)
        self.cmd_interval = int(float(cmd_interval)) if cmd_interval else 1
        if custom_command and custom_command.strip():
            self.command = custom_command.strip()
        else:
            self.command = f"Read_ALLChannels({self.cmd_loop},{self.cmd_interval})"
        self.continuous = continuous
        self.ser = None
        self.running = False
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.thread = None
        self._status_msg = ""

    def start(self):
        if self.running:
            return
        self.running = True
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        self.stop_event.set()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2.0)
        if self.ser and self.ser.is_open:
            try:
                self.ser.close()
            except Exception:
                pass
        self.ser = None

    def _connect(self):
        try:
            self.ser = serial.Serial(self.port, self.baudrate,
                                     timeout=DEFAULT_TIMEOUT,
                                     write_timeout=WRITE_TIMEOUT)
            self.ser.reset_input_buffer()
            self.ser.reset_output_buffer()
            return True
        except Exception as e:
            self.event_bus.emit('status', f"⚠️ 打开串口失败: {e}")
            return False

    def _safe_write(self, data):
        try:
            with self.lock:
                if self.ser is None or not self.ser.is_open:
                    return False
                self.ser.write(data)
                self.ser.flush()
            return True
        except Exception as e:
            self.event_bus.emit('status', f"⚠️ 写入失败: {e}")
            return False

    def _safe_readline(self):
        try:
            with self.lock:
                if self.ser is None or not self.ser.is_open:
                    return None
                raw = self.ser.readline()
                try:
                    line = raw.decode('utf-8', errors='strict')
                except UnicodeDecodeError:
                    line = raw.decode('gbk', errors='ignore')
                return line.strip()
        except Exception:
            return None

    def _run(self):
        retry_count = 0
        if not self._connect():
            self.event_bus.emit('status', "❌ 串口连接失败，读取线程终止")
            self.running = False
            self.event_bus.emit('reader_finished')
            return

        self.event_bus.emit('status', f"▶️ 执行命令: {self.command}")

        while self.running and not self.stop_event.is_set():
            try:
                if self.ser is None or not self.ser.is_open:
                    self.event_bus.emit('status', "🔄 串口断开，尝试重连...")
                    if self._connect():
                        self.event_bus.emit('status', "✅ 重连成功")
                    else:
                        time.sleep(RECONNECT_DELAY)
                        continue

                cmd_with_terminator = self.command + '\r\n'
                if not self._safe_write(cmd_with_terminator.encode('utf-8')):
                    self.ser = None
                    continue

                response_lines = []
                start_t = time.monotonic()
                last_data_t = start_t
                while self.running and not self.stop_event.is_set():
                    line = self._safe_readline()
                    if line is None:
                        now = time.monotonic()
                        if now - start_t > MAX_READ_TIME or now - last_data_t > READ_IDLE_TIMEOUT:
                            break
                        time.sleep(0.05)
                        continue

                    last_data_t = time.monotonic()
                    if line.strip():
                        response_lines.append(line)
                    if line.startswith("OK") or line.startswith("ERROR"):
                        break
                    if time.monotonic() - start_t > MAX_READ_TIME:
                        break
                    if not self.continuous and (line.startswith("END") or line.startswith("完成")):
                        break
                    time.sleep(0.01)

                if not response_lines:
                    retry_count += 1
                    now = datetime.datetime.now()
                    timestamp_str = now.strftime("%Y-%m-%d %H:%M:%S.%f")
                    warn_msg = f"{timestamp_str}  ->  (无响应) 重试 {retry_count}/{MAX_RETRIES}"
                    self.event_bus.emit('data_received', warn_msg, None, None, None, None, None)
                    if retry_count >= MAX_RETRIES:
                        self.event_bus.emit('status', f"⚠️ 连续 {MAX_RETRIES} 次无响应，尝试重连...")
                        self.ser = None
                        retry_count = 0
                        time.sleep(RECONNECT_DELAY)
                    else:
                        time.sleep(0.2)
                    continue
                else:
                    retry_count = 0

                for raw_line in response_lines:
                    now = datetime.datetime.now()
                    timestamp_str = now.strftime("%Y-%m-%d %H:%M:%S.%f")
                    if re.match(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}', raw_line):
                        display_msg = raw_line
                    else:
                        display_msg = f"{timestamp_str}  ->  {raw_line}"
                    dut_num = extract_dut_number(raw_line)
                    val = None
                    value_display = None
                    if dut_num is not None:
                        val = extract_voltage_from_line(raw_line, dut_num)
                        value_display = format_readable(val) if val is not None else None
                    self.event_bus.emit('data_received', display_msg, dut_num, value_display, val, timestamp_str, raw_line)

                if not self.continuous:
                    self.event_bus.emit('data_received', "--- 单次命令执行完成 ---", None, None, None, None, None)
                    self.event_bus.emit('status', "⏹️ 单次命令执行完成")
                    break

                for _ in range(5):
                    if self.stop_event.is_set() or not self.running:
                        break
                    time.sleep(0.1)

            except serial.SerialException as e:
                self.event_bus.emit('status', f"⚠️ 串口异常: {e}，尝试重连...")
                self.ser = None
                time.sleep(RECONNECT_DELAY)
            except Exception as e:
                self.event_bus.emit('status', f"❌ 异常: {e}")
                time.sleep(1)

        if self.ser and self.ser.is_open:
            try:
                self.ser.close()
            except Exception:
                pass
        self.running = False
        self.event_bus.emit('status', "⏹️ 读取线程已停止")
        self.event_bus.emit('reader_finished')
