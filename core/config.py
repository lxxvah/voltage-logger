import os
import sys

DEFAULT_BAUDRATE = 115200
DEFAULT_TIMEOUT = 0.5
WRITE_TIMEOUT = 0.5
READ_IDLE_TIMEOUT = 0.3
MAX_READ_TIME = 3.0
MAX_RETRIES = 3
RECONNECT_DELAY = 1.0
MAX_HISTORY_POINTS = 20000
WAVEFORM_REFRESH_MS = 200
WAVEFORM_WINDOW_SECONDS = 60


def get_exe_dir():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def get_data_dir():
    data_dir = os.path.join(get_exe_dir(), "DAQ_Data")
    if not os.path.exists(data_dir):
        try:
            os.makedirs(data_dir)
        except Exception:
            return get_exe_dir()
    return data_dir


def ensure_dir(path):
    if not os.path.exists(path):
        os.makedirs(path)
    return path
