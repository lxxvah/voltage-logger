import queue
import threading


class EventBus:
    """异步事件总线：避免事件回调阻塞串口读取线程"""

    def __init__(self):
        self._listeners = {}
        self._lock = threading.Lock()
        self._queue = queue.Queue()
        self._stop_event = threading.Event()
        self._dispatcher = threading.Thread(target=self._dispatch_loop, daemon=True)
        self._dispatcher.start()

    def register(self, event, callback):
        with self._lock:
            self._listeners.setdefault(event, []).append(callback)

    def unregister(self, event, callback):
        with self._lock:
            if event in self._listeners:
                try:
                    self._listeners[event].remove(callback)
                except ValueError:
                    pass

    def emit(self, event, *args, **kwargs):
        if self._stop_event.is_set():
            return
        callbacks = []
        with self._lock:
            callbacks = self._listeners.get(event, [])[:]
        for cb in callbacks:
            self._queue.put((cb, args, kwargs))

    def close(self):
        if self._stop_event.is_set():
            return
        self._stop_event.set()
        self._queue.put(None)
        if self._dispatcher.is_alive():
            self._dispatcher.join(timeout=2.0)

    def _dispatch_loop(self):
        while not self._stop_event.is_set():
            item = self._queue.get()
            if item is None:
                self._queue.task_done()
                break
            cb, args, kwargs = item
            worker = threading.Thread(target=self._invoke_callback, args=(cb, args, kwargs), daemon=True)
            worker.start()
            self._queue.task_done()

    def _invoke_callback(self, cb, args, kwargs):
        try:
            cb(*args, **kwargs)
        except Exception as e:
            print(f"事件回调异常: {e}")
