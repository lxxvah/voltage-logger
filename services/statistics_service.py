import csv
import datetime
import os
import threading

from voltage_logger.core.config import ensure_dir, get_data_dir
from voltage_logger.core.utils import format_readable


class StatisticsManager:
    def __init__(self, event_bus):
        self.event_bus = event_bus
        self.stats = [{'active': False, 'count': 0, 'sum': 0.0, 'target': 10, 'samples': []} for _ in range(10)]
        self.stat_csv_counter = 0
        self.lock = threading.Lock()
        event_bus.register('data_received', self.on_data_received)

    def start(self, channel, target):
        with self.lock:
            stat = self.stats[channel]
            if stat['active']:
                self.event_bus.emit('status', f"⚠️ 通道{channel} 统计已在进行中")
                return False
            stat['active'] = True
            stat['count'] = 0
            stat['sum'] = 0.0
            stat['target'] = target
            stat['samples'] = []
        self.event_bus.emit('status', f"🔵 通道{channel} 开始统计 (目标 {target} 个点)")
        return True

    def on_data_received(self, display_msg, dut_num, value_display, voltage_value, timestamp_str, raw_line):
        if dut_num is None or voltage_value is None:
            return

        done = None
        with self.lock:
            stat = self.stats[dut_num]
            if not stat['active']:
                return
            stat['count'] += 1
            stat['sum'] += voltage_value
            stat['samples'].append((timestamp_str, voltage_value))
            if stat['count'] >= stat['target']:
                avg = stat['sum'] / stat['count']
                stat['active'] = False
                samples_copy = list(stat['samples'])
                done = (dut_num, avg, samples_copy)

        if done is None:
            return

        dut_num, avg, samples_copy = done
        avg_str = format_readable(avg)
        now_str = datetime.datetime.now().strftime("%H:%M:%S")
        self.event_bus.emit('status', f"✅ 通道{dut_num} 统计完成，平均值 = {avg_str} ({now_str})")
        self.event_bus.emit('log', f"[统计] 通道{dut_num} 统计完成，平均值 = {avg_str}  (完成时间: {now_str})")
        threading.Thread(target=self._save_stat_csv, args=(dut_num, samples_copy, avg), daemon=True).start()
        self.event_bus.emit('stat_complete', dut_num, avg)

    def _save_stat_csv(self, channel, samples, mean):
        try:
            with self.lock:
                self.stat_csv_counter += 1
                csv_index = self.stat_csv_counter
            now = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            data_dir = get_data_dir()
            mean_dir = ensure_dir(os.path.join(data_dir, "mean_statistics"))
            filename = f"均值_统计{len(samples)}次_通道{channel}_{now}_第{csv_index}次统计.csv"
            filepath = os.path.join(mean_dir, filename)
            with open(filepath, 'w', newline='', encoding='gb18030') as f:
                f.write("sep=,\n")
                writer = csv.writer(f)
                writer.writerow(["序号", "时间戳", "电压"])
                for i, (ts, val) in enumerate(samples, 1):
                    writer.writerow([i, ts, val])
                writer.writerow([])
                writer.writerow(["均值", "", mean])
            self.event_bus.emit('log', f"[统计] 通道{channel} 统计日志已保存: {filename}")
        except Exception as e:
            self.event_bus.emit('log', f"[统计日志保存错误] {e}")
