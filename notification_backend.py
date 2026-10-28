"""Isolated, read-only WinRT notification worker. No permission prompts or clicks."""
import asyncio
import dataclasses
import json
import sys
import time
from automation_policy import load_config
from polling_policy import interval


def emit(**data):
    print(json.dumps(data, ensure_ascii=True), flush=True)


async def wait_for_poll(last_scan, read_config=load_config, clock=time.monotonic, sleep=asyncio.sleep):
    # Heartbeats keep the watchdog alive without querying Windows. Reloading the
    # interval also makes edits effective while waiting for a long interval.
    while True:
        remaining = interval(read_config(), "notifications") - (clock() - last_scan)
        if remaining <= 0:
            return
        emit(type="heartbeat", next_scan_seconds=round(remaining, 1))
        await sleep(min(5, remaining))


async def run(once=False):
    from winrt.windows.ui.notifications.management import UserNotificationListener, UserNotificationListenerAccessStatus
    from winrt.windows.ui.notifications import NotificationKinds
    import winrt.windows.foundation.collections  # Required by the WinRT result projection.
    import winrt.windows.applicationmodel
    from notification_core import Notification, Deduplicator, is_target

    listener = UserNotificationListener.current
    previous_status = None
    previous_interval = None
    dedup = Deduplicator()
    seen_errors = set()
    while True:
        started = time.monotonic()
        seconds = interval(load_config(), "notifications")
        status = listener.get_access_status()
        if status != UserNotificationListenerAccessStatus.ALLOWED:
            emit(type="status", state="blocked", message=f"Windows non consente la lettura delle notifiche (stato {int(status)}). Nessuna richiesta automatica di permesso.")
            return 3
        if previous_status != "allowed":
            emit(type="status", state="starting", message="Accesso Windows consentito. Verifica lettura in corso…")
        try:
            async with asyncio.timeout(8):
                notifications = await listener.get_notifications_async(NotificationKinds.TOAST)
            found = []
            for item in notifications:
                stage = "identificazione mittente"
                try:
                    info = item.app_info
                    app_id = info.app_user_model_id
                    stage = "nome mittente"
                    try:
                        app = info.display_info.display_name
                    except OSError:
                        # Some unpackaged Windows senders expose their ID but not DisplayInfo.
                        app = app_id
                    if not is_target(app, app_id):
                        continue  # Other apps' message contents are not read or saved.
                    stage = "testo notifica"
                    texts = []
                    for binding in item.notification.visual.bindings:
                        texts.extend(element.text for element in binding.get_text_elements() if element.text)
                    if not texts:
                        continue
                    notification = Notification(
                        app=app, app_id=app_id, notification_id=item.id,
                        created=item.creation_time.isoformat(), title=texts[0], body="\n".join(texts[1:]),
                        source="windows-test" if app_id == "Sentinella.Codex.Prova" else "windows",
                    )
                    if dedup.add(notification):
                        found.append(dataclasses.asdict(notification))
                except Exception as exc:
                    message = f"Una notifica non è leggibile ({stage}): {type(exc).__name__}: {exc}"
                    key = (item.id, message)
                    if key not in seen_errors:
                        if len(seen_errors) >= 1000:
                            seen_errors.clear()
                        seen_errors.add(key)
                        emit(type="item_error", message=message)
            emit(type="snapshot", notifications=found)
            if previous_status != "allowed" or previous_interval != seconds:
                emit(type="status", state="active", message=f"Notifiche Windows · controllo ogni {seconds:g} secondi")
            previous_status = "allowed"
            previous_interval = seconds
        except TimeoutError:
            emit(type="status", state="error", message="Windows non ha risposto entro 8 secondi. Riprovo al prossimo controllo.")
            previous_status = "error"
        except Exception as exc:
            emit(type="status", state="error", message=f"Lettura non riuscita: {type(exc).__name__}: {exc}")
            previous_status = "error"
        if once:
            return 0 if previous_status == "allowed" else 2
        await wait_for_poll(started)


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(run("--once" in sys.argv)))
    except Exception as exc:
        emit(type="status", state="error", message=f"Lettore non disponibile: {type(exc).__name__}: {exc}")
        sys.exit(2)
