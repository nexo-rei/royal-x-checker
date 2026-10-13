from __future__ import annotations

import tempfile
from pathlib import Path

from telegram import InputFile


async def send_temporary_document(message, content: bytes, filename: str, caption: str) -> None:
    """Send a short-lived export and always delete its temporary file afterward."""
    with tempfile.TemporaryDirectory(prefix="royal-x-export-") as temporary_directory:
        path = Path(temporary_directory) / filename
        path.write_bytes(content)
        with path.open("rb") as stream:
            await message.reply_document(
                document=InputFile(stream, filename=filename), caption=caption
            )
