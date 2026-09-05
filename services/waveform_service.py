import datetime
import threading
from collections import deque

from voltage_logger.core.config import MAX_HISTORY_POINTS, WAVEFORM_WINDOW_SECONDS


class WaveformManager:
    def __init__(self, event_bus):
        self.event_bus = event_bus
        self.history_queues = [deque(maxlen=MAX_HISTORY_POINTS) for _ in range(10)]
        self.history_lock = threading.Lock()
        self.selected_channel = 0
        event_bus.register('data_received', self.on_data_received)

    def set_channel(self, channel):
        self.selected_channel = channel

    def on_data_received(self, display_msg, dut_num, value_display, voltage_value, timestamp_str, raw_line):
        if dut_num is not None and voltage_value is not None:
            now = datetime.datetime.now()
            with self.history_lock:
                q = self.history_queues[dut_num]
                q.append((now, voltage_value))

    def get_recent_data(self, seconds=WAVEFORM_WINDOW_SECONDS):
        with self.history_lock:
            data = list(self.history_queues[self.selected_channel])
        if not data:
            return []
        now = datetime.datetime.now()
        cutoff = now - datetime.timedelta(seconds=seconds)
        idx = len(data) - 1
        while idx >= 0 and data[idx][0] >= cutoff:
            idx -= 1
        return data[idx + 1:]

    def draw_waveform(self, canvas):
        recent = self.get_recent_data()
        canvas.delete("all")
        w = canvas.winfo_width()
        h = canvas.winfo_height()
        if w < 10 or h < 10:
            w, h = 800, 180

        title = f"通道{self.selected_channel} 电压波形 (共{len(recent)}点)"
        canvas.create_text(w // 2, 18, text=title, font=("Arial", 10, "bold"), fill="black")

        if len(recent) < 2:
            canvas.create_text(w // 2, h // 2, text="等待数据...", font=("Arial", 12), fill="gray")
            return

        now = datetime.datetime.now()
        offsets = [(now - ts).total_seconds() for ts, v in recent]
        volts = [v for ts, v in recent]

        margin = 45
        plot_left = margin
        plot_right = w - margin
        plot_top = margin + 10
        plot_bottom = h - margin

        v_min, v_max = min(volts), max(volts)
        v_range = v_max - v_min
        if v_range < 0.0001:
            v_min -= 1.0
            v_max += 1.0
        else:
            v_min -= v_range * 0.15
            v_max += v_range * 0.15

        canvas.create_text(plot_right - 5, plot_top + 5,
                           text=f"范围: {v_min:.3f} ~ {v_max:.3f}",
                           anchor='ne', font=("Arial", 8), fill="gray")

        def map_x(offset):
            return plot_left + (WAVEFORM_WINDOW_SECONDS - offset) / WAVEFORM_WINDOW_SECONDS * (plot_right - plot_left)

        def map_y(v):
            try:
                return plot_bottom - (v - v_min) / (v_max - v_min) * (plot_bottom - plot_top)
            except Exception:
                return (plot_top + plot_bottom) / 2

        canvas.create_line(plot_left, plot_bottom, plot_right, plot_bottom, fill="black", width=1)
        canvas.create_line(plot_left, plot_top, plot_left, plot_bottom, fill="black", width=1)
        ticks = [0, 30, 60]
        for tick in ticks:
            offset = WAVEFORM_WINDOW_SECONDS - tick
            px = map_x(offset)
            if plot_left <= px <= plot_right:
                label = f"{tick}s"
                canvas.create_text(px, plot_bottom + 5, text=label, anchor='n', font=("Arial", 7))
                canvas.create_line(px, plot_bottom, px, plot_bottom + 3, fill="black", width=1)
        canvas.create_text(plot_left - 5, plot_top, text=f"{v_max:.3f}", anchor='e', font=("Arial", 7))
        canvas.create_text(plot_left - 5, plot_bottom, text=f"{v_min:.3f}", anchor='e', font=("Arial", 7))

        points = []
        for offset, v in zip(offsets, volts):
            if v is not None:
                try:
                    px = map_x(offset)
                    py = map_y(v)
                    if 0 <= px <= w and 0 <= py <= h:
                        points.extend([px, py])
                except Exception:
                    continue

        if len(points) >= 4:
            canvas.create_line(points, fill="blue", width=2, smooth=True)
        else:
            for offset, v in zip(offsets, volts):
                if v is not None:
                    try:
                        px = map_x(offset)
                        py = map_y(v)
                        if 0 <= px <= w and 0 <= py <= h:
                            canvas.create_oval(px - 3, py - 3, px + 3, py + 3, fill="red", outline="red")
                    except Exception:
                        pass
