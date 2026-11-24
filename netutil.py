"""Общие помощники для клиентов сервисов.

pause(sec)   — сон, который раз в секунду проверяет кнопку «Стоп» (раньше «Стоп» ждал конца опроса сервиса — до десятков минут).
patient(fn)  — терпеливый опрос: секундный обрыв сети или 5xx во время ожидания результата не бросает уже оплаченную задачу.
"""
import os
import tempfile
import threading
import time

import requests

CANCEL_CHECK = None   # pipeline ставит сюда check_cancel
MODEL_CONNECT_TIMEOUT = 300
MODEL_READ_TIMEOUT = 300


def response_once(request, progress=None, *, interval=15):
    """Wait for ONE HTTP submission without blocking cancellation/progress.

    requests uses its connect budget for HTTPS proxy CONNECT/TLS as well.
    Cancellation abandons the wait, never resubmits the request, and closes a
    late response. It cannot revoke a task already accepted by the provider.
    The underlying request MUST supply finite transport timeouts.
    """
    check_cancel()
    ready, lock = threading.Event(), threading.Lock()
    state = {'abandoned': False}

    def send():
        response, error = None, None
        try:
            response = request()
        except BaseException as exc:
            error = exc
        with lock:
            abandoned = state['abandoned']
            if not abandoned:
                state.update(response=response, error=error)
        if abandoned and response is not None:
            response.close()
        ready.set()

    started = reported = time.monotonic()
    threading.Thread(target=send, name='model-http-once', daemon=True).start()
    try:
        while not ready.wait(.1):
            check_cancel()
            now = time.monotonic()
            if progress and now - reported >= interval:
                progress(f'ожидаю заголовки ответа: {int(now-started)} с; один запрос, повторной отправки нет. Можно нажать «Стоп».')
                reported = now
        check_cancel()
        if state.get('error') is not None:
            raise state['error']
        return state['response']
    except BaseException:
        with lock:
            state['abandoned'] = True
            response = state.pop('response', None)
        if response is not None:
            response.close()
        raise


def check_cancel():
    if CANCEL_CHECK:
        CANCEL_CHECK()


def pause(sec):
    end = time.monotonic() + max(0.0, float(sec))
    while True:
        check_cancel()
        left = end - time.monotonic()
        if left <= 0:
            return
        time.sleep(min(1.0, left))


def patient(fn, tries=10):
    """Вызвать fn() (обычно один GET статуса). Сетевые ошибки, таймауты, 429 и 5xx — подождать и повторить, до tries раз."""
    if tries < 1:
        raise ValueError("tries must be at least 1")
    last = None
    for attempt in range(tries):
        check_cancel()
        try:
            r = fn()
            code = getattr(r, "status_code", None)
            if isinstance(code, int) and (code >= 500 or code == 429) and attempt < tries - 1:
                last = f"HTTP {code}"
                pause(min(30, 3 * (attempt + 1)))
                continue
            return r
        except requests.HTTPError as e:
            code = getattr(getattr(e, "response", None), "status_code", None)
            if code is not None and code < 500 and code != 429:
                raise
            last = e
        except requests.RequestException as e:
            last = e
        if attempt == tries - 1:
            break
        pause(min(30, 3 * (attempt + 1)))
    if isinstance(last, Exception):
        raise last
    raise requests.ConnectionError(f"сервис не отвечает: {last}")


def save_response(response, dest_path):
    """Publish a download only when the entire stream has arrived.

    A failed download must not look like a valid cached image/video on the next
    run. Keep the temporary file on the destination filesystem for atomic rename.
    """
    temp_path = None
    try:
        response.raise_for_status()
        check_cancel()
        directory = os.path.dirname(os.path.abspath(dest_path))
        with tempfile.NamedTemporaryFile(dir=directory, prefix=".download-", delete=False) as f:
            temp_path = f.name
            for chunk in response.iter_content(1 << 16):
                check_cancel()
                if chunk:
                    f.write(chunk)
        check_cancel()
        if os.path.getsize(temp_path) == 0:
            raise requests.ConnectionError("сервис вернул пустой файл")
        os.replace(temp_path, dest_path)
        temp_path = None
        return dest_path
    finally:
        response.close()
        if temp_path is not None:
            os.unlink(temp_path)
