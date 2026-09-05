import csv
import datetime
import os
import threading

try:
    import openpyxl
    OPENPYXL_AVAILABLE = True
except ImportError:
    OPENPYXL_AVAILABLE = False

from voltage_logger.core.config import ensure_dir, get_data_dir


class ReportGenerator:
    def __init__(self, event_bus):
        self.event_bus = event_bus
        self._running = False
        self._thread = None

    def generate(self, counter, start_time):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._process, args=(counter, start_time), daemon=True)
        self._thread.start()

    def _process(self, counter, start_time):
        try:
            self.event_bus.emit('status', f"📊 正在生成第{counter}次统计报告...")
            data_dir = get_data_dir()
            time_str = start_time.strftime("%Y%m%d_%H%M%S")
            csv_dir = os.path.join(data_dir, "ChannelDataLog")
            pattern = f"通道{{ch}}_{time_str}_第{counter}次.csv"
            channel_data = {}
            channel_avgs = {}
            channel_counts = {}

            for ch in range(10):
                filename = os.path.join(csv_dir, pattern.format(ch=ch))
                if not os.path.isfile(filename):
                    continue
                try:
                    with open(filename, 'r', encoding='gb18030') as f:
                        first_line = f.readline()
                        reader = csv.reader(f)
                        header = next(reader, None)
                        data = []
                        voltages = []
                        for row in reader:
                            if len(row) >= 3:
                                ts = row[0]
                                try:
                                    v = float(row[2])
                                    data.append((ts, v))
                                    voltages.append(v)
                                except ValueError:
                                    continue
                        if data:
                            avg = sum(voltages) / len(voltages)
                            channel_data[ch] = data
                            channel_avgs[ch] = avg
                            channel_counts[ch] = len(voltages)
                except Exception as e:
                    self.event_bus.emit('log', f"[后处理] 读取通道{ch}文件失败: {e}")

            if not channel_data:
                self.event_bus.emit('log', "[后处理] 未找到任何通道数据文件，跳过统计")
                return

            now = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            result_folder_name = f"通道数据统计结果_{now}_第{counter}次统计"
            result_folder = ensure_dir(os.path.join(data_dir, result_folder_name))

            if OPENPYXL_AVAILABLE:
                self._generate_excel(result_folder, channel_data, channel_avgs, channel_counts, counter, now)
            else:
                self._generate_csv_summary(result_folder, channel_avgs, channel_counts, counter, now)

            self.event_bus.emit('log', f"[后处理] 统计报告已生成，保存在 {result_folder}")
            self.event_bus.emit('status', "✅ 统计报告生成完成")
        except Exception as e:
            self.event_bus.emit('log', f"[后处理] 生成报告时发生异常: {e}")
            self.event_bus.emit('status', f"❌ 报告生成失败: {e}")
        finally:
            self._running = False

    def _generate_excel(self, folder, channel_data, channel_avgs, channel_counts, counter, now):
        wb = openpyxl.Workbook()
        wb.remove(wb.active)
        for ch, data in channel_data.items():
            sheet = wb.create_sheet(title=f"通道{ch}")
            avg = channel_avgs[ch]
            count = channel_counts.get(ch, 0)
            sheet.append(["平均值", avg, "数据点数", count])
            sheet.append(["时间戳", "电压"])
            for ts, v in data:
                sheet.append([ts, v])
            sheet.column_dimensions['A'].width = 22
            sheet.column_dimensions['B'].width = 18
        filename = f"通道汇总报告_{now}_第{counter}次统计.xlsx"
        filepath = os.path.join(folder, filename)
        wb.save(filepath)

    def _generate_csv_summary(self, folder, channel_avgs, channel_counts, counter, now):
        filename = f"通道均值汇总_{now}_第{counter}次统计.csv"
        filepath = os.path.join(folder, filename)
        with open(filepath, 'w', newline='', encoding='gb18030') as f:
            f.write("sep=,\n")
            writer = csv.writer(f)
            writer.writerow(["通道", "均值", "数据点数"])
            for ch in sorted(channel_avgs.keys()):
                writer.writerow([ch, channel_avgs[ch], channel_counts.get(ch, 0)])
        self.event_bus.emit('log', "[后处理] 未安装openpyxl，仅生成CSV汇总文件")
