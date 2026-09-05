import re
import serial
import serial.tools.list_ports


def format_readable(value_float):
    if value_float is None:
        return "无数据"
    try:
        return f"{value_float:.9f}"
    except Exception:
        return str(value_float)


def extract_voltage_from_line(raw_line, dut_num=None):
    if not raw_line:
        return None
    if dut_num is not None:
        pattern = r'DUT[:]' + str(dut_num) + r'\s*'
        cleaned = re.sub(pattern, '', raw_line, flags=re.IGNORECASE)
    else:
        cleaned = raw_line
    match = re.search(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", cleaned)
    if match:
        try:
            return float(match.group())
        except Exception:
            return None
    return None


def extract_dut_number(raw_line):
    match = re.search(r'DUT[:](\d+)', raw_line, re.IGNORECASE)
    if match:
        return int(match.group(1))
    return None


def get_available_ports():
    return [port.device for port in serial.tools.list_ports.comports()]
