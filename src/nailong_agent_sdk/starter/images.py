# Copyright (c) 2026 David Michael Indraputra

"""Pictures to show the model: a file becomes a size-checked, base64 attachment."""

from __future__ import annotations

import base64
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from ..agent.provider_context import ATTACHED_IMAGES_KEY

MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_IMAGES = 10
MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


def load_image(path: Path) -> dict[str, str]:
    media_type = MEDIA_TYPES.get(path.suffix.lower())
    if media_type is None:
        raise ValueError(
            f'"{path.name}" is not a supported image type; use one of {sorted(MEDIA_TYPES)}.'
        )
    if not path.is_file():
        raise ValueError(f'The image file "{path}" does not exist.')
    data = path.read_bytes()
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError(
            f'"{path.name}" is {len(data)} bytes; the limit for one image is {MAX_IMAGE_BYTES}.'
        )
    return {"media_type": media_type, "data": base64.b64encode(data).decode("ascii")}


def with_images(task_input: dict[str, Any], images: Sequence[Path | dict[str, str]]) -> None:
    if not images:
        return
    if len(images) > MAX_IMAGES:
        raise ValueError(f"{len(images)} images were given; the limit is {MAX_IMAGES}.")
    loaded = [load_image(item) if isinstance(item, Path) else dict(item) for item in images]
    task_input[ATTACHED_IMAGES_KEY] = loaded
