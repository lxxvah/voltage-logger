import csv
import datetime
import os
import queue
import re
import threading

from voltage_logger.core.config import ensure_dir, get_data_dir


class TxtLogger:
    def __init__(self, event_bus):
        self.event_bus = event_bus
        self.record_counter = 0
        self.is_recording = False
        self.file_handles = {}
        self.lock = threading.Lock()
        self._write_queue = queue.Queue()
        self._writer_thread = threading.Thread(target=self._write_loop, daemon=True)
        self._writer_thread.start()
        event_bus.register('data_received', self.on_data_received)

    def _write_loop(self):
        while True:
            item = self._write_queue.get()
            if item is None:
                self._write_queue.task_done()
                break
            dut_num, content = item
            try:
                with self.lock:
                    f = self.file_handles.get(dut_num)
                    if f is None:
                        continue
                    f.write(content + "\n")
                    f.flush()
            except Exception as e:
                self.event_bus.emit('log', f"❌ 写入TXT DUT{dut_num} 失败: {e}")
            finally:
                self._write_queue.task_done()

    def start_recording(self):
        if self.is_recording:
            return False, "记录已开启"
        self.record_counter += 1
        record_num = self.record_counter
        data_dir = get_data_dir()
        txt_dir = ensure_dir(os.path.join(data_dir, "ChannelDataLogTXT"))
        try:
            for i in range(10):
                filename = os.path.join(txt_dir, f"DUT{i}_第{record_num}次.txt")
                f = open(filename, 'w', encoding='gb18030', buffering=1)
                self.file_handles[i] = f
        except Exception as e:
            return False, f"无法创建文本日志: {e}"
        self.is_recording = True
        self.event_bus.emit('status', f"📝 TXT日志记录中 (第{record_num}次) -> {txt_dir}")
        self.event_bus.emit('log', f"[系统] TXT日志记录已开启 (第{record_num}次)")
        return True, f"第{record_num}次"

    def stop_recording(self):
        if not self.is_recording:
            return
        self.is_recording = False
        with self.lock:
            for f in self.file_handles.values():
                try:
                    f.close()
                except Exception:
                    pass
            self.file_handles.clear()
        self.event_bus.emit('status', "⏹️ TXT日志记录已停止")
        self.event_bus.emit('log', "[系统] TXT日志记录已停止")

    def on_data_received(self, display_msg, dut_num, value_display, voltage_value, timestamp_str, raw_line):
        if not self.is_recording or dut_num is None or raw_line is None:
            return
        try:
            if re.match(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}', raw_line):
                content = raw_line
            else:
                content = f"{timestamp_str} {raw_line}"
            self._write_queue.put((dut_num, content))
        except Exception as e:
            self.event_bus.emit('log', f"❌ 写入TXT DUT{dut_num} 失败: {e}")


class CsvLogger:
    def __init__(self, event_bus, report_generator):
        self.event_bus = event_bus
        self.report_generator = report_generator
        self.real_csv_counter = 0
        self.real_csv_enabled = False
        self.real_csv_files = {}
        self.real_csv_line_counts = {}
        self.csv_flush_threshold = 20
        self.csv_lock = threading.Lock()
        self._current_csv_counter = None
        self._csv_start_time = None
        self._csv_processed = False
        self._write_queue = queue.Queue()
        self._writer_thread = threading.Thread(target=self._write_loop, daemon=True)
        self._writer_thread.start()
        event_bus.register('data_received', self.on_data_received)
        event_bus.register('reader_finished', self.on_reader_finished)

    def _write_loop(self):
        while True:
            item = self._write_queue.get()
            if item is None:
                self._write_queue.task_done()
                break
            dut_num, timestamp_str, voltage_value, raw_line = item
            try:
                with self.csv_lock:
                    file_entry = self.real_csv_files.get(dut_num)
                    if file_entry is None:
                        continue
                    f, writer = file_entry
                    writer.writerow([timestamp_str, dut_num, voltage_value, raw_line])
                    count = self.real_csv_line_counts.get(dut_num, 0) + 1
                    self.real_csv_line_counts[dut_num] = count
                    if count >= self.csv_flush_threshold:
                        f.flush()
                        self.real_csv_line_counts[dut_num] = 0
            except Exception as e:
                self.event_bus.emit('log', f"CSV写入错误 (通道{dut_num}): {e}")
            finally:
                self._write_queue.task_done()

    def start_real_csv(self):
        if self.real_csv_enabled:
            return False, "CSV已开启"
        self.real_csv_counter += 1
        now = datetime.datetime.now()
        self._csv_start_time = now
        self._current_csv_counter = self.real_csv_counter
        data_dir = get_data_dir()
        csv_dir = ensure_dir(os.path.join(data_dir, "ChannelDataLog"))
        base_name = f"通道{{ch}}_{now.strftime('%Y%m%d_%H%M%S')}_第{self.real_csv_counter}次.csv"
        try:
            for ch in range(10):
                filename = os.path.join(csv_dir, base_name.format(ch=ch))
                f = open(filename, 'w', newline='', encoding='gb18030')
                f.write("sep=,\n")
                writer = csv.writer(f)
                writer.writerow(["时间戳", "通道", "电压", "原始行"])
                f.flush()
                self.real_csv_files[ch] = (f, writer)
                self.real_csv_line_counts[ch] = 0
            self.real_csv_enabled = True
            self._csv_processed = False
            self.event_bus.emit('status', f"📝 CSV日志已开启 (第{self.real_csv_counter}次) -> {csv_dir}")
            self.event_bus.emit('log', f"[系统] CSV日志已开启 (第{self.real_csv_counter}次)")
            return True, f"第{self.real_csv_counter}次"
        except Exception as e:
            self.stop_real_csv()
            return False, str(e)

    def stop_real_csv(self):
        if not self.real_csv_enabled:
            return
        self.real_csv_enabled = False
        with self.csv_lock:
            for ch, (f, writer) in self.real_csv_files.items():
                try:
                    f.flush()
                    f.close()
                except Exception:
                    pass
            self.real_csv_files.clear()
            self.real_csv_line_counts.clear()
        if not self._csv_processed and self._current_csv_counter is not None:
            self._csv_processed = True
            self.report_generator.generate(self._current_csv_counter, self._csv_start_time)
        self.event_bus.emit('status', "CSV日志已关闭")
        self.event_bus.emit('log', "[系统] CSV日志已关闭")

    def on_data_received(self, display_msg, dut_num, value_display, voltage_value, timestamp_str, raw_line):
        if not self.real_csv_enabled or dut_num is None or voltage_value is None:
            return
        self._write_queue.put((dut_num, timestamp_str, voltage_value, raw_line))

    def on_reader_finished(self):
        pass
