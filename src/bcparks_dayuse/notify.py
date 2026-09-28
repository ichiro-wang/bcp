import asyncio
import base64
import logging
import shutil

log = logging.getLogger(__name__)

# Windows' own PowerShell AppUserModelID, so toasts work without registering an app.
_POWERSHELL_APP_ID = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"

_TOAST_SCRIPT = """
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
$xml = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$text = $xml.GetElementsByTagName('text')
$text[0].AppendChild($xml.CreateTextNode({title})) | Out-Null
$text[1].AppendChild($xml.CreateTextNode({message})) | Out-Null
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{app_id}').Show($toast)
"""


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _command(title: str, message: str) -> list[str] | None:
    if shutil.which("powershell.exe"):  # Windows or WSL
        script = (
            _TOAST_SCRIPT.replace("{title}", _ps_quote(title))
            .replace("{message}", _ps_quote(message))
            .replace("{app_id}", _POWERSHELL_APP_ID)
        )
        encoded = base64.b64encode(script.encode("utf-16-le")).decode()
        return ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded]
    if shutil.which("notify-send"):  # Linux desktop
        return ["notify-send", title, message]
    return None


async def notify(title: str, message: str) -> None:
    """Show a desktop notification; failures are logged, never raised."""
    command = _command(title, message)
    if command is None:
        log.warning("No desktop notifier found; %s: %s", title, message)
        return
    try:
        proc = await asyncio.create_subprocess_exec(
            *command, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
        )
        _, stderr = await proc.communicate()
        if proc.returncode:
            log.warning("Notification failed: %s", stderr.decode(errors="replace").strip())
    except OSError:
        log.exception("Notification failed")
